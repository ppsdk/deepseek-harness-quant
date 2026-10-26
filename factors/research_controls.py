"""Validated per-experiment parameters shared by charts, screening and backtests."""
from copy import deepcopy
import json
import math


def policy():
    from factors.research_library import read_config
    return read_config('research_controls')


def group_for(name, spec, cfg):
    if spec.get('kind') == 'pattern':
        return 'pattern:' + spec['family']
    for key, item in cfg['shared_groups'].items():
        if spec.get('operation') in item['operations']:
            return key
    return name


def catalogue(definitions=None):
    if definitions is not None:
        return _catalogue(definitions)
    from factors.research_library import BASE,factor_definitions
    from data.research_cache import metadata,file_stamp
    names=('research_controls','research_patterns','research_ml','research_factors','factor_library','alpha101')
    paths=[BASE/'config'/(name+suffix) for name in names for suffix in ('.yaml','.yaml.example')]
    return metadata('indicator_parameter_catalogue',tuple(file_stamp(p) for p in paths),
                    lambda:_catalogue(factor_definitions()))


def _catalogue(definitions):
    from factors.research_library import factor_definitions, read_config
    definitions = definitions if definitions is not None else factor_definitions()
    cfg, groups = policy(), {}
    families = read_config('research_patterns')['families']
    combined = {**{k: dict(v, kind='technical') for k, v in cfg['legacy_indicators'].items()}, **definitions}
    for name, spec in combined.items():
        if spec.get('kind') not in ('technical', 'pattern'):
            continue
        key = group_for(name, spec, cfg)
        base = families[spec['family']] if spec['kind'] == 'pattern' else spec
        fields = {k: dict(cfg['parameter_fields'][k], default=v)
                  for k, v in base.get('params', {}).items()
                  if k in cfg['parameter_fields'] and isinstance(v, (float, int)) and not isinstance(v, bool)}
        if not fields:
            continue
        group = groups.setdefault(key, dict(label=cfg['shared_groups'].get(key, {}).get('label', base['label']),
                                            fields=fields, factors=[]))
        group['factors'].append(name)
    return groups


def validate(values, definitions=None):
    if isinstance(values, str):
        if len(values) > 50000:
            raise ValueError('指标参数内容过长。')
        try:
            values = json.loads(values)
        except (ValueError, TypeError):
            raise ValueError('指标参数必须是合法的JSON对象。') from None
    if not isinstance(values, dict) or len(values) > policy()['max_indicator_groups']:
        raise ValueError('指标参数应为对象，且不能一次修改过多指标。')
    groups, clean = catalogue(definitions), {}
    for key, params in values.items():
        if key not in groups or not isinstance(params, dict) or set(params) - set(groups[key]['fields']):
            raise ValueError('指标或参数不存在：' + str(key))
        out = {}
        for field, value in params.items():
            spec = groups[key]['fields'][field]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(spec['label'] + '必须是有限数值。')
            if not spec['min'] <= value <= spec['max'] or (spec.get('integer') and not float(value).is_integer()):
                raise ValueError(spec['label'] + '应在' + str(spec['min']) + '到' + str(spec['max']) + '之间' +
                                 ('，并填写整数。' if spec.get('integer') else '。'))
            out[field] = int(value) if spec.get('integer') else float(value)
        merged = {k: v['default'] for k, v in groups[key]['fields'].items()} | out
        for lo, hi, message in [('fast', 'slow', '短周期必须小于长周期。'),
                                ('oversold', 'overbought', '低位门槛必须小于高位门槛。'),
                                ('min_separation', 'max_separation', '最短拐点间隔必须小于最长间隔。')]:
            if lo in merged and hi in merged and merged[lo] >= merged[hi]:
                raise ValueError(message)
        if out:
            clean[key] = out
    return clean


def dependencies(names, definitions):
    seen = set()
    def visit(name):
        if name in seen:
            return
        seen.add(name)
        spec = definitions.get(name, {})
        for dep in spec.get('dependencies', spec.get('recipe', {}).get('features', [])):
            visit(dep)
    for name in names:
        visit(name)
    return seen


def apply(panels, values, definitions=None):
    from factors.research_library import factor_definitions, read_config
    definitions = definitions if definitions is not None else factor_definitions()
    cfg = policy()
    families = deepcopy(panels.get('_pattern_config') or {})
    base_families = read_config('research_patterns')['families']
    effective = deepcopy(panels.get('_indicator_specs') or {})
    for name, item in {**cfg['legacy_indicators'], **definitions}.items():
        spec = effective.get(name, item)
        key = group_for(name, spec, cfg)
        if key not in values:
            continue
        if spec.get('kind') == 'pattern':
            family = spec['family']
            families.setdefault(family, deepcopy(base_families[family]))['params'].update(values[key])
        else:
            effective[name] = deepcopy(spec)
            effective[name]['kind'] = 'technical'
            effective[name].setdefault('params', {}).update(values[key])
    panels['_indicator_specs'], panels['_pattern_config'] = effective, families
    panels['_indicator_params'] = deepcopy(values)
    panels.pop('_factor_cache', None)
    panels.pop('_pattern_cache', None)


def warmup_days(values):
    cfg, groups = policy(), catalogue()
    largest = 0
    for key, params in values.items():
        merged = {k: v['default'] for k, v in groups[key]['fields'].items()} | params
        periods = [v for k, v in merged.items() if cfg['parameter_fields'][k].get('integer')]
        if periods:
            largest = max(largest, sum(periods) * cfg['warmup_calendar_multiplier'])
        if key == 'pattern:dow':
            largest = max(largest, (merged['left'] + merged['right']) * 120)
    return int(largest)


def labels(values, groups=None):
    groups = catalogue() if groups is None else groups
    return [dict(id=key, label=groups[key]['label'],
                 parameters=[dict(label=groups[key]['fields'][k]['label'], value=v) for k, v in params.items()])
            for key, params in values.items() if key in groups]
