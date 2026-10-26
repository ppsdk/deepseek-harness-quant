"""Persistent declarative strategies, shared screening and cached chart data."""
import hashlib
from contextlib import closing
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
from copy import deepcopy

import pandas as pd
import yaml

BASE = Path(__file__).resolve().parent.parent
CUSTOM = BASE / 'config/research_strategies.yaml'
SAVE_LOCK = threading.Lock()


class StrategyConflict(ValueError):
    pass


def custom_strategies():
    path = CUSTOM if CUSTOM.exists() else CUSTOM.with_suffix('.yaml.example')
    if not path.exists():
        return {}
    from factors.research_library import _read
    data = deepcopy(_read(str(path),path.stat().st_mtime_ns))
    return data.get('strategies', {})


def revision():
    return hashlib.sha256(json.dumps(custom_strategies(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def workspace_config():
    from backtest.research_runner import research_config
    with SAVE_LOCK:
        return {'ok': True, **research_config(), 'revision': revision()}


def save_strategy(values):
    from backtest.research_runner import research_config, validate_params
    if not isinstance(values, dict):
        raise ValueError('策略必须为 JSON 对象。')
    key = values.get('id', '')
    if not isinstance(key, str) or not re.fullmatch(r'[a-z][a-z0-9_]{1,63}', key):
        raise ValueError('策略 ID 需为 2—64 位小写字母、数字或下划线，以字母开头。')
    name, desc = values.get('name'), values.get('desc', '')
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        raise ValueError('请填写不超过 80 字的策略名称。')
    if not isinstance(desc, str) or len(desc) > 1000:
        raise ValueError('策略说明不得超过 1000 字。')
    with SAVE_LOCK:
        cfg = research_config()
        current = custom_strategies()
        if key in cfg['strategies'] and not cfg['strategies'][key]['editable']:
            raise StrategyConflict('内置策略不可覆盖，请使用新的策略 ID。')
        if values.get('revision') != revision():
            raise StrategyConflict('策略目录已变化，请刷新后再保存。')
        factors = values.get('factor_list')
        if not isinstance(factors, list) or not 1 <= len(factors) <= len(cfg['factor_catalog']):
            raise ValueError('请选择至少一个已安装因子。')
        cleaned, seen = [], set()
        for item in factors:
            if not isinstance(item, dict) or not isinstance(item.get('name'), str):
                raise ValueError('因子格式错误。')
            f = item['name']
            if f not in cfg['factor_catalog'] or f in seen:
                raise ValueError('因子未安装或重复：' + f)
            try:
                sign, weight = float(item.get('sign', 1)), float(item.get('weight', 1))
            except (TypeError, ValueError):
                raise ValueError('因子方向或权重格式错误。') from None
            if sign not in (-1, 1) or not math.isfinite(weight) or not 0 < weight <= 100:
                raise ValueError('因子方向只能为 1/-1，权重需大于 0 且不超过 100。')
            entry = {'name': f, 'sign': int(sign), 'weight': weight}
            for bound in ('min', 'max'):
                if bound in item and item[bound] not in ('', None):
                    try:
                        value = float(item[bound])
                    except (TypeError, ValueError):
                        raise ValueError('筛选条件的上下限必须为数字。') from None
                    if not math.isfinite(value):
                        raise ValueError('筛选条件必须是有限数值。')
                    entry[bound] = value
            if entry.get('min', -math.inf) > entry.get('max', math.inf):
                raise ValueError('筛选下限不能大于上限。')
            cleaned.append(entry)
            seen.add(f)
        defaults = values.get('defaults', {})
        allowed=set(cfg.get('strategy_default_keys',['topn','stocks','rebalance_days']))
        if not isinstance(defaults, dict) or set(defaults) - allowed:
            raise ValueError('策略默认值包含不支持的参数。')
        item = {'name': name.strip(), 'desc': desc.strip(), 'category': '自定义',
                'instant': True, 'factor_list': cleaned, 'defaults': defaults}
        cfg['strategies'][key] = item
        validated = validate_params({'strategy': key}, cfg)
        item['defaults'] = {k: validated[k] for k in set(defaults)|{'topn','stocks','rebalance_days'}}
        current[key] = item
        CUSTOM.parent.mkdir(parents=True, exist_ok=True)
        scratch = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=CUSTOM.parent,
                                             prefix='research_', suffix='.tmp', delete=False) as handle:
                scratch = Path(handle.name)
                yaml.safe_dump({'strategies': current}, handle, allow_unicode=True, sort_keys=False)
            os.replace(scratch, CUSTOM)
        finally:
            if scratch is not None and scratch.exists():
                scratch.unlink()
        return {'ok': True, 'strategy': key, 'revision': revision(), 'message': '策略已保存，可选股或回测。'}


def _names():
    from data.cache import CACHE_DIR
    path = CACHE_DIR / 'stock_basic.db'
    if not path.exists():
        return {}
    from data.research_cache import metadata,file_stamp
    def load():
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as con:
            return dict(con.execute('SELECT code,name FROM stock_basic'))
    return metadata('stock_names',file_stamp(path),load)


def screen(values):
    from backtest.research_runner import research_config, validate_params, load_panels, factor_score, rank_candidates
    if not isinstance(values, dict):
        raise ValueError('请求必须为 JSON 对象。')
    cfg = research_config()
    values = dict(values)
    # A screening signal uses only the requested close and its trailing history.
    values['start'] = values.get('end') or cfg['parameters']['end']['default']
    p = validate_params(values, cfg)
    panels, data = load_panels(p, cfg)
    days = panels['close'].index[panels['close'].index <= p['end']]
    if not len(days):
        raise ValueError('该日期之前没有缓存行情。')
    day = days[-1]
    spec = cfg['strategies'][p['strategy']]
    scores = factor_score(panels, spec['factor_list'])
    selected = rank_candidates(panels, scores, day, p['topn'])
    components = {item['name']: factor_score(panels, [item]).loc[day] for item in spec['factor_list']}
    names = _names()
    rows = [{'rank': i + 1, 'code': code, 'name': names.get(code, ''), 'score': float(score),
             'close': float(panels['close'].at[day, code]),
             'factor_ranks': {f: float(rank[code]) for f, rank in components.items()}}
            for i, (code, score) in enumerate(selected.items())]
    result = {'ok': True, 'state': 'ready' if rows else 'no_candidates', 'signal_date': day,
              'requested_date': p['end'], 'strategy': p['strategy'], 'strategy_spec': spec,
              'params': p, 'data': data, 'candidates': rows,
              'message': '收盘研究排名，下一交易日才可尝试成交；不产生买入指令或写入持仓。',
              'warnings': ['股票池为当前已缓存股票，存在覆盖和幸存者偏差；未知历史 ST 不视为已排除。',
                           '缺少任一策略因子值、收盘价或成交量的股票不参与排名；少于持仓上限时不补足。']}
    if not rows:
        result['warnings'].append('没有满足数据条件的候选，请检查热身期、换手字段或扩大股票池。')
        if any(f['name'].startswith('fund_') for f in spec['factor_list']):
            result['warnings'].append('这一天可能尚无可用财务记录。新获取的报表从获取日的次日起使用，不会回填到更早年份；请核对基本面数据表中的可用日期。')
    return result


def chart(query):
    from data.research_charts import chart_data
    code=query.get('code','').strip().upper()
    if not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',code):raise ValueError('请提供完整股票代码，如000001.SZ。')
    return chart_data(code,_names().get(code,''),query)


def navigation():
    path = BASE / 'config/navigation.yaml'
    if not path.exists():
        path = path.with_suffix('.yaml.example')
    return {'ok': True, **yaml.safe_load(path.read_text(encoding='utf-8'))}
