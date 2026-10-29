"""Configuration-owned indicator catalogue and one calculation entry point."""
from copy import deepcopy
from contextlib import closing
from functools import lru_cache
import json
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd
import yaml

BASE = Path(__file__).resolve().parent.parent


def read_config(name):
    path = BASE / 'config' / (name + '.yaml')
    template = path.with_suffix('.yaml.example')
    data = deepcopy(_read(str(template), template.stat().st_mtime_ns))
    if path.exists():
        def merge(left, right):
            for key, value in right.items():
                if isinstance(value, dict) and isinstance(left.get(key), dict):
                    merge(left[key], value)
                else:
                    left[key] = value
        merge(data, deepcopy(_read(str(path), path.stat().st_mtime_ns)))
    return data


@lru_cache(maxsize=128)
def _read(path, stamp):
    return yaml.safe_load(Path(path).read_text(encoding='utf-8')) or {}


def library_config():
    return read_config('factor_library')


def factor_definitions():
    from factors.research_manager import custom_factors
    base = {**library_config()['factors'], **read_config('alpha101')['factors'],
            **read_config('research_patterns')['factors'], **read_config('research_ml')['factors'], **custom_factors()}
    # Four-axis mining is an input to the same platform, not a report-only
    # side channel.  Effective records are exposed as stable ``mined_*``
    # aliases so a later strategy can select them without another page or
    # another calculation path.  The source definition remains authoritative.
    path = BASE / 'output' / 'strategy_research' / 'four_axis_v1' / 'factor_results.json'
    try:
        records = json.loads(path.read_text(encoding='utf-8')).get('records', []) if path.exists() else []
    except (OSError, ValueError, TypeError):
        records = []
    for record in records:
        if not record.get('effective'):
            continue
        rid = str(record.get('id', '')).strip()
        if not rid:
            continue
        alias = 'mined_' + rid
        definition = record.get('definition') or {}
        source = definition.get('source_factor') or ('axis_' + rid)
        spec = base.get(source)
        if spec is None and definition.get('operation'):
            op = str(definition['operation'])
            spec = next((candidate for candidate in base.values()
                         if candidate.get('operation') in (op, 'axis_' + op)), None)
        if spec is None:
            operation = definition.get('operation')
            if not operation:
                continue
            spec = dict(definition, kind='technical', operation=operation if str(operation).startswith('axis_') else 'axis_' + str(operation), chart=False)
            spec.setdefault('requires', ['close'])
        else:
            spec = deepcopy(spec)
        if alias in base:
            # A user/configured factor wins over a generated alias with the
            # same name; mining must never silently replace an explicit spec.
            continue
        spec.update(id=alias, label=record.get('label') or spec.get('label') or rid,
                    description=(spec.get('description') or '') + '（四类因子挖掘有效候选；需在新的样本区间复核。）',
                    category='挖掘因子 · ' + str(record.get('category', '研究')), mined_from=rid,
                    evaluation=record.get('evaluation') or {},
                    mining_evaluation=record.get('evaluation') or {})
        if not spec.get('evaluation'):
            from factors.research_evaluation import axis_evaluation
            spec['evaluation'] = axis_evaluation(record)
            spec['mining_evaluation'] = spec['evaluation']
        base[alias] = spec
    return base


def pattern_requirements(names, definitions=None):
    definitions = definitions if definitions is not None else factor_definitions()
    visited, families, days = set(), set(), 0
    def visit(name):
        nonlocal days
        if name in visited:return
        visited.add(name); item=definitions.get(name,{})
        days=max(days,int(item.get('warmup_calendar_days',0)))
        if item.get('kind')=='pattern':families.add(item['family'])
        for dep in item.get('dependencies',item.get('recipe',{}).get('features',[])):visit(dep)
    for name in names:visit(name)
    return days,families


def data_status():
    from data.cache import DailyCache
    from data.research_fundamentals import coverage, DB
    from data.research_cache import metadata,file_stamp
    cache, adjusts = DailyCache(), set()
    from data.research_valuation import settings as valuation_settings,coverage as valuation_coverage
    valuation_db,_=valuation_settings()
    paths=[Path(path) for path in [cache.db_path,*cache._inc_paths()]]
    stamp=tuple(file_stamp(p) for p in [*paths,DB,valuation_db])
    def load():
        for path in paths:
            if not path.exists():continue
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=10)) as con:
                adjusts.update(adjust for code,adjust in con.execute('SELECT code,adjust FROM bar_meta') if not (code.endswith('.SH') and not code.startswith('6')))
        return {'raw_prices': 'none' in adjusts, 'fundamentals': coverage(),'valuations':valuation_coverage()}
    return metadata('research_data_coverage',stamp,load)


def catalogue(base=None):
    status = data_status()
    out = {}
    # A factor may have no run yet.  Keep a stable, explicit evaluation shape
    # so the directory never makes an untested factor look like a tested one.
    evaluation_defaults = {
        'state': 'pending', 'rank_ic': None, 'ic_mean': None, 'ic_std': None,
        'ic_ir': None, 'ic_positive_rate': None, 'ic_tstat': None,
        'coverage': None, 'evaluated_days': 0, 'group_spread': None,
        'long_short_spread': None, 'group_spread_days': 0,
        'mean_forward_return': None, 'best_group': None, 'worst_group': None,
        'sample_stocks_mean': None, 'sample_stocks_min': None,
    }
    for key, item in {**(base or {}), **factor_definitions()}.items():
        m = dict(item)
        reasons = []
        for field in m.get('requires', []):
            if field in ('raw_close', 'vwap') and not status['raw_prices']:
                reasons.append('缺同一天的不复权收盘价，无法把成交均价与前复权价格对齐')
            elif field == 'cap' and 'cap' not in status['fundamentals']:
                reasons.append('缺历史总市值')
            elif field == 'industry_history':
                reasons.append('缺当时的行业分类')
            elif field.startswith('fund:') and field[5:] not in status['fundamentals']:
                reasons.append('尚未取得或导入这项财务数据')
            elif field.startswith('valuation:') and field[10:] not in status.get('valuations',{}):
                reasons.append('尚未取得该历史估值字段')
        m['editable'] = m.get('kind')=='expression'
        evaluation = dict(evaluation_defaults, **(m.get('evaluation') or {}))
        # Reuse an actually saved analysis when available.  This is read-only
        # metadata; missing archives remain ``pending`` instead of receiving a
        # guessed score.
        try:
            analysis_dir = BASE / 'output' / 'factor_analysis'
            candidates=[]
            if analysis_dir.exists():
                for path in analysis_dir.glob('*.json'):
                    try:
                        record=__import__('json').loads(path.read_text(encoding='utf-8'))
                        if record.get('factor') == key:
                            candidates.append((record.get('generated_at',''),record))
                    except (OSError,ValueError):
                        continue
            if candidates:
                record=max(candidates,key=lambda item:item[0])[1]
                for field in evaluation_defaults:
                    if field in record and record[field] is not None:
                        evaluation[field]=record[field]
                evaluation['rank_ic']=record.get('ic_mean')
                evaluation['state']='ready' if record.get('evaluated_days',0) else 'insufficient'
        except OSError:
            pass
        # ``rank_ic`` is the public catalogue name; stored analyses use ic_mean.
        if evaluation.get('rank_ic') is None and evaluation.get('ic_mean') is not None:
            evaluation['rank_ic'] = evaluation['ic_mean']
        if evaluation.get('evaluated_days', 0) and evaluation.get('rank_ic') is not None:
            evaluation['state'] = 'ready'
        elif reasons:
            evaluation['state'] = 'insufficient'
        m.update(id=key, category=m.get('category', '基础指标'), evaluation=evaluation,
                 status='已检验' if evaluation['state']=='ready' else ('数据不足' if reasons else '尚未检验'),
                 available=not reasons, data_note='；'.join(dict.fromkeys(reasons)) or '可计算；具体股票和日期还需有完整数据')
        if m.get('kind') == 'fundamental' and not reasons:
            m['data_note'] = '已有记录，最早可用日期：' + status['fundamentals'][m['metric']]['first_date']
        if any(f.startswith('valuation:') for f in m.get('requires',[])) and not reasons:
            m['data_note']='仅缓存样本股票的供应商历史估值序列，按日期精确匹配；未核验原始财报版本，不能用最新估值回填历史。'
        out[key] = m
    return out


def raw_factor(name, panels, definitions=None):
    # Panels are a per-request snapshot; calculated matrices never cross requests.
    memo = panels.setdefault('_factor_cache', {})
    if name in memo:
        return memo[name]
    definitions = definitions if definitions is not None else factor_definitions()
    if name in panels.get('_indicator_specs', {}):
        definitions = {**definitions, name: panels['_indicator_specs'][name]}
    if name in definitions:
        spec = definitions[name]
        if spec['kind'] == 'technical':
            from factors.research_indicators import calculate
            raw = calculate(spec, panels)
        elif spec['kind'] == 'pattern':
            from factors.research_patterns import calculate
            raw = calculate(spec, panels)
        elif spec['kind'] == 'expression':
            from factors.alpha101_engine import evaluate
            inputs={dep:raw_factor(dep,panels,definitions) for dep in spec['dependencies']}
            inputs.update({f:panels[f] for f in spec['requires'] if f in panels and ':' not in f})
            raw=evaluate(spec['formula'],panels,inputs)
        elif spec['kind'] == 'ml':
            from backtest.research_ml import predict_walkforward
            raw,_=predict_walkforward(panels,spec['recipe'],definitions)
        elif spec['kind'] == 'fundamental':
            field = 'fund:' + spec['metric']
            if field not in panels:
                raise ValueError('缺少财务数据：' + spec['label'] + '。请先获取财报或导入带可用日期的 CSV。')
            raw = panels[field]
        else:
            for field in spec.get('requires', []):
                if field == 'industry_history':
                    raise ValueError('该 Alpha101 需要当时的行业分类，当前未接入。')
                if field not in panels or not panels[field].notna().any().any():
                    raise ValueError('该 Alpha101 缺少数据：' + field + '。请查看指标库中的缺失说明。')
            from factors.alpha101_engine import evaluate
            raw = evaluate(spec['formula'], panels)
    else:
        from factors.factor_engine import FACTOR_FUNCS
        c = panels['close'].astype(float)
        if name == 'turn_20d': raw = panels['turn'].rolling(20, min_periods=20).mean()
        elif name == 'lowvol_60': raw = c.pct_change(fill_method=None).rolling(60).std()
        elif name == 'new_high_250':
            hi = c.rolling(250, min_periods=125).max()
            raw = c.eq(hi).astype(float).where(hi.notna() & c.notna())
        elif name in FACTOR_FUNCS: raw = FACTOR_FUNCS[name](c)
        else: raise ValueError('指标不存在：' + name)
    memo[name] = raw.replace([np.inf, -np.inf], np.nan).where(panels['close'].notna())
    return memo[name]


def attach_extra_panels(panels, codes, start, end, factors, cache, definitions=None):
    definitions = factor_definitions() if definitions is None else definitions
    def requirements(name):
        spec=definitions.get(name,{})
        out=set(spec.get('requires',[]))
        deps=spec.get('dependencies',[]) if spec.get('kind')!='ml' else spec['recipe']['features']
        for dep in deps:out.update(requirements(dep))
        return out
    required=set().union(*(requirements(f['name']) for f in factors))
    valuation_fields={r[10:] for r in required if r.startswith('valuation:')}
    if valuation_fields:
        from data.research_valuation import panels as valuation_panels
        for metric,value in valuation_panels(panels['close'].index,codes,valuation_fields).items():
            panels['valuation:'+metric]=value
    wanted = {r[5:] for r in required if r.startswith('fund:')}
    if 'cap' in required:
        wanted.add('cap')
    if wanted:
        from data.research_fundamentals import panels as finance_panels
        for metric, value in finance_panels(panels['close'].index, codes, wanted).items():
            panels['fund:' + metric] = value
            if metric == 'cap': panels['cap'] = value
    if required & {'vwap', 'raw_close'}:
        raw = cache.get_daily_batch(codes, start=start, end=end, adjust='none', fields=['close'])
        raw_close = pd.DataFrame({code: df.set_index('date')['close'] for code, df in raw.items()}).reindex_like(panels['close'])
        panels['raw_close'] = raw_close
        # Raw turnover / raw shares is not a qfq price: adjust with the same-day ratio.
        panels['vwap'] = panels['amount'] / panels['volume'].replace(0, np.nan) * panels['close'] / raw_close.replace(0, np.nan)
