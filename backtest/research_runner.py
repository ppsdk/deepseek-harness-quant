"""Daily-bar research with prior-close signals and an auditable cash/share ledger."""
import json
from contextlib import closing
import hashlib
import math
from pathlib import Path
import re
import sqlite3
import time
from datetime import date, timedelta
from uuid import uuid4

import numpy as np
import pandas as pd
import yaml

from data.cache import DailyCache
from factors.factor_engine import FACTOR_FUNCS

BASE = Path(__file__).resolve().parent.parent
CONFIG = BASE / 'config/research.yaml'
ARCHIVES = BASE / 'output/backtest_archive'
INTEGER_PARAMS = {'topn', 'stocks', 'rebalance_days'}


def research_config():
    from factors.research_library import catalogue, read_config, _read
    from copy import deepcopy
    template_path=CONFIG.with_suffix('.yaml.example')
    template=deepcopy(_read(str(template_path),template_path.stat().st_mtime_ns))
    cfg=deepcopy(_read(str(CONFIG),CONFIG.stat().st_mtime_ns)) if CONFIG.exists() else template
    cfg.setdefault('factor_catalog', template.get('factor_catalog', {}))
    cfg.setdefault('chart_max_bars', template.get('chart_max_bars', 160))
    cfg['factor_catalog'] = catalogue(cfg['factor_catalog'])
    services=read_config('research_services')
    from backtest.research_pools import list_pools
    pools=list_pools()['pools']
    cfg['stock_pools']=pools
    cfg['chart_settings']=services['chart']
    pattern_config=read_config('research_patterns')
    cfg['pattern_configuration']=pattern_config['families']
    cfg['chart_settings']['pattern_presets']=pattern_config['chart_presets']
    cfg['parameter_groups']=services.get('backtest_form_groups', [])
    from factors.research_controls import policy, catalogue as parameter_catalogue
    controls=policy()
    for key, spec in controls['parameters'].items():
        cfg['parameters'].setdefault(key,spec)
    cfg['parameter_groups'] += controls['parameter_groups']
    cfg['indicator_controls']=parameter_catalogue()
    cfg['strategy_default_keys']=controls['strategy_default_keys']
    cfg['position_note']=controls['position_note']
    cfg['trigger_labels']=controls['trigger_labels']
    cfg['workflow_steps']=controls['workflow_steps']
    cfg['page_sizes']=controls['page_sizes']
    from backtest.research_signals import plans
    cfg['signal_plans']=plans()['plans']
    cfg['parameters']['signal_plan_id']={**cfg['parameters']['signal_plan_id'],'options':{'':'不使用候选股信号方案',**{r['id']:r['name'] for r in cfg['signal_plans']}}}
    cfg['parameters']['pool_id']={'label':'保存的股票池','type':'select','default':'','options':{'':'使用下面的股票代码或缓存范围',**{r['id']:r['name']+'（'+str(r['count'])+'只）' for r in pools}}}
    cfg['parameters']['timing_rule']={'label':'什么时候允许持有股票','type':'select','default':'none','options':{k:v['label'] for k,v in services['timing']['rules'].items()}}
    cfg['parameters']['timing_index']={'label':'择时看哪个指数','type':'select','default':services['timing']['benchmark']['code'],'options':{k:v['name'] for k,v in services['market']['indices'].items()}}
    cfg['parameters']['timing_execution']={'label':'什么时候检查市场风险','type':'select','default':services['timing']['default_execution'],'options':services['timing']['executions']}
    cfg['alpha101_source'] = read_config('alpha101')['source']
    cfg['strategies'] = {**read_config('research_presets')['strategies'], **pattern_config['strategies'], **cfg['strategies']}
    from backtest.research_workspace import custom_strategies
    custom = custom_strategies()
    builtin_ids = set(cfg['strategies'])
    cfg['strategies'] = {**custom, **cfg['strategies']}  # Built-in IDs cannot be overwritten.
    for key, field in cfg['parameters'].items():
        if key == 'end' and field['default'] is None:
            field['default'] = date.today().isoformat()
    cfg['strategies'] = {key: {**item, 'id': key,
                              'editable': key in custom and key not in builtin_ids,
                              'factors': [f['name'] for f in item['factor_list']]}
                         for key, item in cfg['strategies'].items()}
    for item in cfg['strategies'].values():
        missing = [cfg['factor_catalog'][f['name']]['label'] for f in item['factor_list']
                   if not cfg['factor_catalog'][f['name']]['available']]
        item['available'] = not missing
        item['data_note'] = '需要补充数据：' + '、'.join(missing) if missing else '可选股和回测；收益尚未验证'
    return cfg


def list_strategies():
    return research_config()['strategies']


def validate_params(values, cfg):
    if not isinstance(values, dict):
        raise ValueError('请求必须为 JSON 对象。')
    if set(values) - (set(cfg['parameters']) | {'strategy'}):
        raise ValueError('存在未知回测参数。')
    p = {key: field['default'] for key, field in cfg['parameters'].items()}
    p['strategy'] = values.get('strategy', cfg['default_strategy'])
    if not isinstance(p['strategy'], str) or p['strategy'] not in cfg['strategies']:
        raise ValueError('策略不存在，请从当前策略目录中选择。')
    p.update(cfg['strategies'][p['strategy']].get('defaults', {}))
    p.update(values)
    for key, field in cfg['parameters'].items():
        if field['type'] == 'number':
            try:
                value = float(p[key])
            except (ValueError, TypeError):
                raise ValueError(f'{key} 必须是数值。') from None
            if not math.isfinite(value) or not field['min'] <= value <= field['max']:
                raise ValueError(f'{key} 必须在 {field["min"]} 到 {field["max"]} 之间。')
            if (key in INTEGER_PARAMS or field.get('integer')) and not value.is_integer():
                raise ValueError(f'{key} 必须是整数。')
            p[key] = int(value) if key in INTEGER_PARAMS or field.get('integer') else value
        elif field['type']=='object':
            from factors.research_controls import validate as validate_indicators
            p[key]=validate_indicators(p[key])
        elif field['type']=='select':
            if p[key] not in field['options']:raise ValueError('请选择有效的'+field['label']+'。')
        elif field['type'] == 'date':
            try:
                p[key] = date.fromisoformat(str(p[key])).isoformat()
            except ValueError:
                raise ValueError(f'{key} 日期格式应为 YYYY-MM-DD。') from None
    if p['start'] > p['end']:
        raise ValueError('开始日期不能晚于结束日期。')
    from backtest.research_pools import apply_pool
    if p.get('signal_plan_id'):
        from backtest.research_signals import plans
        plan=plans({'id':p['signal_plan_id']})
        if p.get('pool_id') and p['pool_id']!=plan['pool_id']:raise ValueError('信号方案与股票池不一致，请选择方案对应的名单。')
        p['pool_id']=plan['pool_id']
    apply_pool(p)
    if p['topn'] > p['stocks']:
        raise ValueError('最多选几只股票，不能超过本次参与选股的股票数。')
    if not isinstance(p['codes'], str):
        raise ValueError('codes 必须是逗号分隔的股票代码。')
    if any(f['name'] == 'turn_20d' or 'turn' in cfg['factor_catalog'].get(f['name'],{}).get('requires',[]) for f in cfg['strategies'][p['strategy']]['factor_list']) and p['start'] < '2019-01-01':
        raise ValueError('换手类因子只允许 2019 年及以后。')
    return p


def load_panels(p, cfg, definitions=None):
    cache = DailyCache()
    from factors.research_library import factor_definitions
    from backtest.research_signals import prepare,attach
    definitions,signal_plan=prepare(p,cfg,definitions if definitions is not None else factor_definitions())
    wanted=cfg['strategies'][p['strategy']]['factor_list']+([{'name':'signal_entry'},{'name':'signal_exit'}] if signal_plan else [])
    # include holding_filter factor in warmup calculation
    hf_cfg = cfg['strategies'][p['strategy']].get('holding_filter')
    if hf_cfg and hf_cfg.get('factor'):
        wanted = wanted + [{'name': hf_cfg['factor']}]
    from factors.research_library import pattern_requirements
    extra_days,pattern_families=pattern_requirements([f['name'] for f in wanted],definitions)
    from factors.research_controls import warmup_days
    history_days=max(cfg['warmup_calendar_days'],extra_days,warmup_days(p.get('indicator_params',{})),
                     int(p.get('allocation_lookback',20)*2))
    warmup = (date.fromisoformat(p['start']) - timedelta(days=history_days)).isoformat()
    available = set()
    for db in [cache.db_path, *cache._inc_paths()]:
        if not Path(db).exists():
            continue
        with closing(sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True, timeout=10)) as con:
            available.update(row[0] for row in con.execute(
                'SELECT DISTINCT code FROM daily_bar WHERE adjust=? AND date BETWEEN ? AND ?',
                ('qfq', warmup, p['end'])))
    available = sorted(code for code in available if re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', code))
    universe = p.get('universe', 'cache')
    if universe and universe.startswith('index:'):
        symbol = universe.split(':', 1)[1]
        try:
            from backtest.dual_momentum import constituents as _get_constituents
            from factors.research_library import read_config as _rc
            _dm_cfg = _rc('dual_momentum')
            item, warn = _get_constituents(symbol, _dm_cfg)
            wanted_set = set(item.get('codes', []))
            available = [c for c in available if c in wanted_set]
            if not available:
                raise ValueError('指数 %s 成分股在本机缓存中没有数据，请先下载数据。' % symbol)
        except ImportError:
            pass  # dual_momentum not available, fall through to cache
    if p['codes'].strip():
        codes = sorted({c.strip().upper() for c in p['codes'].split(',') if c.strip()})
        if any(c not in available for c in codes):
            raise ValueError('指定代码在缓存中不存在，请使用 000001.SZ 等完整代码。')
        if len(codes) > p['stocks']:
            raise ValueError('指定代码数量超过股票数上限。')
    else:
        codes = available[:p['stocks']]
    if len(codes) < p['topn']:
        raise ValueError('可用股票少于持仓上限，请降低持仓上限或等待下载完成。')
    fields = ['open', 'high', 'low', 'close', 'volume', 'amount', 'turn', 'is_st']
    frames = cache.get_daily_batch(codes, start=warmup, end=p['end'], adjust='qfq', fields=fields)
    if not frames:
        raise ValueError('没有可读日线，请先下载数据。')
    indexed = {code: frame.set_index('date') for code, frame in frames.items()}
    panels = {field: pd.DataFrame({code: frame[field] for code, frame in indexed.items()}).sort_index()
              for field in fields}
    from factors.research_library import attach_extra_panels
    attach_extra_panels(panels, codes, warmup, p['end'], wanted, cache,definitions)
    # ── holding filter panel ──────────────────────────────────────────────────
    hf = cfg['strategies'][p['strategy']].get('holding_filter')
    if hf and hf.get('factor') and hf['factor'] in definitions:
        from factors.research_library import raw_factor
        try:
            panels['_holding_filter'] = raw_factor(hf['factor'], panels, definitions)
        except Exception:
            pass
    p['holding_filter'] = hf or None
    from backtest.research_timing import attach_timing
    attach_timing(panels,p,cache,warmup,p['end'])
    fingerprint = hashlib.sha256()
    for field, panel in panels.items():
        if not isinstance(panel, pd.DataFrame):
            continue
        fingerprint.update(field.encode())
        fingerprint.update(','.join(panel.columns).encode())
        fingerprint.update(pd.util.hash_pandas_object(panel, index=True).values.tobytes())
    from backtest.research_pools import get_pool
    panels['_pattern_config']={k:v for k,v in cfg.get('pattern_configuration',{}).items() if k in pattern_families}
    from factors.research_controls import apply as apply_indicators
    apply_indicators(panels,p.get('indicator_params',{}),definitions)
    attach(panels,definitions,signal_plan)
    return panels, {'available_stocks': len(available), 'selected_codes': codes,
                    'warmup_calendar_days':history_days,
                    'stock_pool': get_pool(p['pool_id']) if p.get('pool_id') else None,
                    'selection': 'explicit' if p['codes'].strip() else 'cached_codes_sorted',
                    'price_adjustment': 'qfq', 'rows': sum(len(f) for f in frames.values()),
                    'snapshot_hash': fingerprint.hexdigest()}


def factor_score(panels, factors):
    from factors.research_library import factor_definitions, raw_factor
    definitions = factor_definitions()
    parts, weights = [], []
    eligible = None
    for spec in factors:
        name, sign = spec['name'], float(spec.get('sign', 1))
        if name in definitions or name in panels.get('_indicator_specs',{}):
            raw = raw_factor(name, panels, definitions)
        elif name == 'turn_20d':
            raw = panels['turn'].rolling(20, min_periods=20).mean()
        elif name == 'lowvol_60':
            raw = panels['close'].pct_change(fill_method=None).rolling(60).std()
        elif name == 'new_high_250':
            hi = panels['close'].rolling(250, min_periods=125).max()
            raw = panels['close'].eq(hi).astype(float).where(hi.notna() & panels['close'].notna())
        elif name in FACTOR_FUNCS:
            raw = FACTOR_FUNCS[name](panels['close'].astype(float))
        else:
            raise ValueError('未安装的因子: ' + name)
        weight = float(spec.get('weight', 1))
        if not math.isfinite(weight) or weight <= 0 or sign not in (-1, 1):
            raise ValueError('因子权重必须为正数，方向必须为 1 或 -1。')
        parts.append((raw * sign).rank(axis=1, pct=True) * weight)
        weights.append(weight)
        limits = raw.notna()
        if 'min' in spec: limits &= raw >= spec['min']
        if 'max' in spec: limits &= raw <= spec['max']
        eligible = limits if eligible is None else eligible & limits
    if not parts:
        raise ValueError('策略需要至少一个因子。')
    result=(sum(parts) / sum(weights)).where(eligible)
    if '_timing' in panels:result=result.where(panels['_timing'].eq(1))
    return result  # Require every selected condition.


def rank_candidates(panels, score, signal_day, topn):
    """Shared research ranking for screening and execution; no recommendation side effects."""
    sc = score.loc[signal_day].dropna()
    valid = (panels['close'].loc[signal_day] > 0) & (panels['volume'].loc[signal_day] > 0)
    sc = sc[valid.reindex(sc.index).fillna(False)]
    sc = sc[panels['is_st'].loc[signal_day].reindex(sc.index).ne(1)]
    return sc.sort_values(ascending=False, kind='stable').head(topn)


def simulate(panels, score, p):
    from backtest.research_portfolio import simulate as portfolio_simulate
    return portfolio_simulate(panels, score, p)


def run_research(values, *, archive=True):
    from backtest.bt_report import compute_metrics
    started = time.monotonic()
    cfg = research_config()
    p = validate_params(values, cfg)
    meta = cfg['strategies'][p['strategy']]
    panels, data = load_panels(p, cfg)
    scores = factor_score(panels, meta['factor_list'])
    return build_result(panels,data,scores,p,cfg,meta,started,archive=archive)


def _grade_verdict(metrics: dict) -> str:
    """五档结论分级：基于年化收益、夏普比率、最大回撤综合打分。

    档位    | 年化    | 夏普   | 最大回撤  | 含义
    --------|---------|--------|-----------|-----------------------------
    优秀    | ≥15%    | ≥1.2   | >-25%     | 风险调整后表现突出，可重点研究
    良好    | ≥8%     | ≥0.7   | >-35%     | 有实用价值，需结合市场环境评估
    观察    | >0%     | >0     | >-50%     | 正收益但整体一般，留作参考
    勉强    | >-5%    | —      | —         | 微亏或持平，策略逻辑或参数待优化
    无效    | ≤-5%    | —      | —         | 明显亏损，该参数组合不可用

    注：优秀/良好需同时满足收益、夏普、回撤三项；观察只需正收益且夏普>0。
    """
    if not metrics:
        return '无效'
    ar = metrics.get('annual_return', -999)
    sh = metrics.get('sharpe', 0)
    dd = metrics.get('max_drawdown', -999)   # 负值，越接近0越好

    if ar >= 0.15 and sh >= 1.2 and dd > -0.25:
        return '优秀'
    if ar >= 0.08 and sh >= 0.7 and dd > -0.35:
        return '良好'
    if ar > 0 and sh > 0 and dd > -0.50:
        return '观察'
    if ar > -0.05:
        return '勉强'
    return '无效'


def build_result(panels,data,scores,p,cfg,meta,started,archive=True):
    from backtest.bt_report import compute_metrics
    sim = simulate(panels, scores, p)
    ret, nav = sim['returns'], sim['nav_series']
    if len(ret) < 2:
        raise ValueError('区间内交易日不足，请检查日期与缓存覆盖。')
    metrics = compute_metrics(ret)
    peaks = np.maximum.accumulate(np.r_[1., nav.values])[1:]
    metrics['max_drawdown'] = float((nav.values / peaks - 1).min())
    metrics['calmar'] = metrics['annual_return'] / abs(metrics['max_drawdown']) if metrics['max_drawdown'] < 0 else 0.
    bench = panels['close'].pct_change(fill_method=None).mean(axis=1).reindex(ret.index.strftime('%Y-%m-%d')).fillna(0)
    bench.index = ret.index
    bench_nav = (1 + bench).cumprod()
    fees = {key: sum(t[key] for t in sim['trades']) for key in ('commission', 'stamp_tax', 'slippage_cost')}
    fees['total'] = sum(fees.values())
    warnings = [
        '股票池来自当前已缓存代码，非历史时点完整股票池，存在幸存者偏差；未指定代码时按代码排序截取。',
        '历史 ST、退市回收和涨跌停精确价缺失：剔除已知 ST；一字行情保守禁止买卖，其他涨跌停未精确模拟。',
        '使用前复权价格及小数调整份额，未模拟真实整手、分红现金流、过户费、盘口冲击和成交量参与率。',
        '卖出印花税使用本次设置的固定费率；2019—2023 年历史税率变化未自动分段。',
        '缺失/停牌日不生成可成交价格；已有持仓沿用最后估值并记录陈旧估值天数，期末不强制平仓。',
        '全池等权曲线仅为未计成本的日收益参考，不是可交易指数。',
    ]
    if len(ret) < 60:
        warnings.append('样本不足 60 个交易日。')
    if not sim['trades']:
        warnings.append('本次没有成交，请检查因子热身期、字段覆盖和持仓数。')
    if any(f['name'].startswith('alpha101_') for f in meta['factor_list']):
        warnings.extend(cfg['alpha101_source']['notes'])
    if any(f['name'].startswith('fund_') for f in meta['factor_list']):
        warnings.append('财务数据仅从记录的可用日期起使用；新抓报表从抓取日的次日起生效，无法据此检验更早年份。历史 CSV 的版本和可用日期由提供者保证。')
    if p.get('timing_rule','none')!='none':warnings.append(('市场风险每日检查，未满足时次日开盘尝试退出；恢复后只在换股日重新买入。' if p.get('timing_execution')=='daily_exit' else '市场择时只在换股日检查，次日开盘尝试成交。')+'无法成交时保留持仓并记录原因；指数缺值时不生成持有信号。')
    warnings.append('一字行情和停牌过滤使用成交日完整日线作保守事后筛查；它不是只依赖开盘时已知信息的真实撮合。')
    warnings.append(cfg.get('position_note',''))
    if panels.get('_signal_plan'):
        warnings.append('使用固定候选名单和单独的入场/退出条件；每日用前收盘检查，只在有空位且条件满足时尝试新建仓。普通换股日不强制卖出，技术退出优先于其他个股退出；出现买卖条件冲突时不新建仓。候选名单不回填到确定日期之前。')
    run_id = uuid4().hex
    result = {'ok': True, 'engine': 'research_ledger_v1', 'run_id': run_id,
              'name': f'research_{p["strategy"]}_{run_id}', 'title': meta['name'], 'category': meta['category'],
              'strategy': p['strategy'], 'factors': meta['factors'],
              'strategy_spec': meta, 'warmup_calendar_days': data.get('warmup_calendar_days',cfg['warmup_calendar_days']),
              'pattern_configuration':dict(panels.get('_pattern_config',{})),
              'indicator_configuration':dict(panels.get('_indicator_specs',{})),
              'signal_plan':panels.get('_signal_plan'),
              'verdict': _grade_verdict(metrics),
              'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
              'metrics': metrics, 'params': p, 'data': data, 'costs': fees, 'warnings': warnings,
              'dates': list(ret.index.strftime('%Y-%m-%d')), 'nav': list(nav.values),
              'bench_nav': list(bench_nav.values), 'excess_nav': list((nav / bench_nav).values),
              'excess_annual': metrics['annual_return'] - compute_metrics(bench)['annual_return'],
              'returns': [{'date': str(d)[:10], 'ret': float(r)} for d, r in ret.items()],
              'trades': sim['trades'], 'rejections': sim['rejections'], 'final_cash': sim['cash'],
              'daily_account': sim['daily_account'],
              'position_details':sim['position_details'],'realized_pnl':sim['realized_pnl'],
              'final_positions': sim['positions'], 'stale_valuation_days': sim['stale_valuation_days'],
              'elapsed_s': round(time.monotonic() - started, 3), 'archived': archive}
    from backtest.research_runtime import snapshot as runtime_snapshot
    result['provenance']=runtime_snapshot()
    from backtest.research_report import build_report
    result['report'] = build_report(result)
    return save_result(result) if archive else result


def save_result(result):
    result['archived']=True
    ARCHIVES.mkdir(parents=True,exist_ok=True)
    target=ARCHIVES/(result['name']+'.json')
    result['archive_url']='/dl/backtest_archive/'+target.name
    target.write_text(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=1),encoding='utf-8')
    return result
