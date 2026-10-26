"""Configuration-owned indicator catalogue and one calculation entry point."""
from copy import deepcopy
from contextlib import closing
from functools import lru_cache
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
    return {**library_config()['factors'], **read_config('alpha101')['factors'],
            **read_config('research_patterns')['factors'], **read_config('research_ml')['factors'], **custom_factors()}


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
    paths=[Path(path) for path in [cache.db_path,*cache._inc_paths()]]
    stamp=tuple(file_stamp(p) for p in [*paths,DB])
    def load():
        for path in paths:
            if not path.exists():continue
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=10)) as con:
                adjusts.update(adjust for code,adjust in con.execute('SELECT code,adjust FROM bar_meta') if not (code.endswith('.SH') and not code.startswith('6')))
        return {'raw_prices': 'none' in adjusts, 'fundamentals': coverage()}
    return metadata('research_data_coverage',stamp,load)


def catalogue(base=None):
    status = data_status()
    out = {}
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
        m['editable'] = m.get('kind')=='expression'
        m.update(id=key, category=m.get('category', '基础指标'), status='尚未验证收益',
                 available=not reasons, data_note='；'.join(dict.fromkeys(reasons)) or '可计算；具体股票和日期还需有完整数据')
        if m.get('kind') == 'fundamental' and not reasons:
            m['data_note'] = '已有记录，最早可用日期：' + status['fundamentals'][m['metric']]['first_date']
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
