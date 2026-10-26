"""User-owned expression factors, dated analyses, and experiment annotations."""
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import time
from functools import lru_cache
from copy import deepcopy
from uuid import uuid4

import numpy as np
import pandas as pd
import yaml

BASE = Path(__file__).resolve().parent.parent
CUSTOM = BASE / 'config/research_factors.yaml'
LOCK = threading.RLock()
ANALYSES = BASE / 'output/factor_analysis'
META = BASE / 'data/cache/research_experiments.db'


def custom_factors():
    path = CUSTOM if CUSTOM.exists() else CUSTOM.with_suffix('.yaml.example')
    from factors.research_library import _read
    return deepcopy(_read(str(path),path.stat().st_mtime_ns)).get('factors', {}) if path.exists() else {}


def revision():
    return hashlib.sha256(json.dumps(custom_factors(),sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def expression_names(formula):
    from factors.alpha101_engine import parse, ALLOWED_FUNCTIONS, FUNCTION_ARITY
    if not isinstance(formula,str) or not 0 < len(formula) <= 4000:
        raise ValueError('公式需为 1—4000 个字符。')
    try:tree=parse(formula)
    except ValueError as e:raise ValueError(str(e).replace('Alpha101 ','')) from None
    names=set(); count=0
    def walk(node,depth=0):
        nonlocal count
        count+=1
        if count>800 or depth>50:raise ValueError('公式过于复杂，请拆成几项因子。')
        if node[0]=='name': names.add(node[1])
        elif node[0]=='call':
            if node[1] not in ALLOWED_FUNCTIONS:raise ValueError('不支持这个公式函数：'+node[1])
            lo,hi=FUNCTION_ARITY[node[1]]
            if not lo<=len(node[2])<=hi:raise ValueError(node[1]+' 的参数数量不正确。')
            for child in node[2]:walk(child,depth+1)
        elif node[0] in ('unary','binary','where'):
            for child in node[1:]:
                if isinstance(child,tuple):walk(child,depth+1)
    walk(tree)
    return names


def save_factor(payload):
    from factors.research_library import factor_definitions
    with LOCK:
        definitions=factor_definitions(); current=custom_factors(); key=payload.get('id','')
        if not isinstance(key,str) or not re.fullmatch(r'[a-z][a-z0-9_]{1,63}',key):
            raise ValueError('编号使用 2—64 位小写字母、数字或下划线，以字母开头。')
        if key in definitions and key not in current:raise ValueError('不能覆盖内置指标，请另取编号。')
        if payload.get('revision')!=revision():raise ValueError('因子目录已被修改，请重新加载后保存。')
        label=payload.get('label','').strip(); description=payload.get('description','').strip()
        category=payload.get('category','自定义因子').strip()
        if not label or len(label)>80 or len(description)>2000 or not category or len(category)>40:
            raise ValueError('请填写名称和分类，并缩短过长的说明。')
        from factors.research_formula import compile_formula
        spec=compile_formula(payload.get('formula',''),definitions,key)
        try:sign=int(payload.get('default_sign',1))
        except (TypeError,ValueError):raise ValueError('排序方向只能为 1 或 -1。') from None
        if sign not in (-1,1):raise ValueError('排序方向只能为 1 或 -1。')
        entry=dict(spec,label=label,description=description,category=category,default_sign=sign,
                   overlay=bool(payload.get('overlay',False)))
        current[key]=entry
        # Recheck all existing descendants when replacing a dependency.
        graph={**definitions,key:entry}
        def cycle(node,stack):
            if node in stack:raise ValueError('修改后会造成因子循环引用。')
            for child in graph.get(node,{}).get('dependencies',[]):cycle(child,stack|{node})
        for node in graph:cycle(node,set())
        for custom_key,m in current.items():
            refreshed=compile_formula(m['formula'],graph,custom_key)
            m.update(requires=refreshed['requires'],warmup_calendar_days=refreshed['warmup_calendar_days'],chart=True)
        CUSTOM.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=CUSTOM.parent,delete=False,suffix='.tmp') as out:
            yaml.safe_dump({'factors':current},out,allow_unicode=True,sort_keys=False);scratch=out.name
        try:os.replace(scratch,CUSTOM)
        finally:
            if os.path.exists(scratch):os.unlink(scratch)
        return dict(ok=True,id=key,revision=revision(),message='因子已保存，可检验效果或加入策略。')


def analyse(payload):
    from backtest.research_runner import research_config,validate_params,load_panels
    from factors.research_library import raw_factor
    cfg=research_config(); key=payload.get('factor')
    if key not in cfg['factor_catalog']:raise ValueError('请选择目录中的因子。')
    policy=__import__('factors.research_library',fromlist=['read_config']).read_config('research_services')['factor_analysis']
    horizon=int(payload.get('horizon',policy['horizons'][0]));groups=int(payload.get('groups',policy['groups']))
    lo,hi=policy['group_bounds']
    if horizon not in policy['horizons'] or not lo<=groups<=hi:raise ValueError(f'请选择支持的持有天数和 {lo}—{hi} 个分组。')
    meta={'factor_list':[{'name':key}]};cfg['strategies']['factor_analysis']=meta
    p=validate_params({k:v for k,v in payload.items() if k in cfg['parameters']}|{'strategy':'factor_analysis'},cfg)
    from factors.research_diagnostics import clean,future_returns,diagnostics
    panels,data=load_panels(p,cfg);preprocessing=payload.get('preprocessing','none');raw=clean(raw_factor(key,panels),preprocessing,policy)
    # Buy next open, measure until the open after h trading intervals. No same-close fills.
    future=future_returns(panels,horizon)
    ranks=raw.rank(axis=1,pct=True);ics=[];group_values={g:[] for g in range(1,groups+1)};coverage=[]
    for day in raw.index[(raw.index>=p['start'])&(raw.index<=p['end'])]:
        pair=pd.DataFrame({'factor':raw.loc[day],'future':future.loc[day]}).dropna()
        coverage.append(float(raw.loc[day].notna().mean()))
        if len(pair)<policy['min_stocks'] or pair.factor.nunique()<2:continue
        ic=pair.factor.rank().corr(pair.future.rank())
        if pd.notna(ic):ics.append({'date':day,'value':float(ic),'stocks':len(pair)})
        # Equal factor values stay in the same group; no arbitrary code-based tie split.
        bins=np.ceil(pair.factor.rank(pct=True,method='average')*groups).clip(1,groups)
        for g in group_values:
            vals=pair.future[bins==g]
            if len(vals):group_values[g].append(float(vals.mean()))
    series=pd.Series([r['value'] for r in ics],dtype=float)
    result=dict(ok=True,run_id=uuid4().hex,factor=key,label=cfg['factor_catalog'][key]['label'],factor_spec=cfg['factor_catalog'][key],
                generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),horizon=horizon,params=p,data=data,
                ic_mean=float(series.mean()) if len(series) else None,ic_std=float(series.std()) if len(series)>1 else None,
                evaluated_days=len(ics),coverage=float(np.mean(coverage)) if coverage else 0,ic_series=ics,
                groups=[{'group':g,'mean_forward_return':float(np.mean(v)) if v else None,'days':len(v)} for g,v in group_values.items()],
                warnings=['这里检查因子排序与以后涨跌是否相关，不是完整交易回测；分组收益没有扣费用。',
                          '持有区间可能重叠，样本之间并不独立；没有据此计算显著性或宣称有效。',
                          '仅使用已经下载的股票，可能遗漏历史退市股票；缺值不补造。'])
    result.update(diagnostics(raw,panels,p['start'],p['end'],policy),preprocessing=preprocessing,
                  ic_ir=float(series.mean()/series.std()) if len(series)>1 and series.std()>0 else None)
    result['warnings'].append('ICIR为相关系数均值除以样本标准差，未年化；自相关表示不同日期横截面排序的延续，不是显著性证明。预处理只用当天股票数据；未做行业或市值中性化。')
    from factors.research_controls import dependencies
    from factors.research_library import factor_definitions
    definitions=factor_definitions()
    used=dependencies([key],definitions)
    result['factor_definitions']={k:definitions[k] for k in used if k in definitions}
    result['indicator_configuration']=panels.get('_indicator_specs',{})
    from backtest.research_runtime import snapshot
    result['provenance']=snapshot()
    ANALYSES.mkdir(parents=True,exist_ok=True)
    (ANALYSES/(result['run_id']+'.json')).write_text(json.dumps(result,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    return result


@lru_cache(maxsize=128)
def _experiment_summary(filename,stamp,size):
    d=json.loads(Path(filename).read_text(encoding='utf-8'))
    record={k:d.get(k) for k in ('run_id','title','strategy','category','generated_at','params','metrics','costs','engine')}
    record['selected_count']=len(d.get('data',{}).get('selected_codes',[]))
    return record


def experiment_list(query):
    from backtest.research_runner import ARCHIVES
    records=[];annotations={}
    if META.exists():
        with closing(sqlite3.connect(META)) as con:
            annotations={r[0]:{'favorite':bool(r[1]),'note':r[2]} for r in con.execute('SELECT id,favorite,note FROM notes')}
    for path in ARCHIVES.glob('research_*.json'):
        try:
            stat=path.stat();record=deepcopy(_experiment_summary(str(path),stat.st_mtime_ns,stat.st_size))
            record.update(id=path.stem,annotation=annotations.get(path.stem,{'favorite':False,'note':''}),download='/dl/backtest_archive/'+path.name)
            if query.get('q','').lower() not in json.dumps(record,ensure_ascii=False).lower():continue
            records.append(record)
        except (ValueError,OSError):continue
    records.sort(key=lambda r:r.get('generated_at',''),reverse=True)
    return dict(ok=True,runs=records)


def analysis_records(query):
    key=query.get('id')
    if key:
        if not re.fullmatch(r'[0-9a-f]{32}',key):raise ValueError('因子检验编号无效。')
        path=ANALYSES/(key+'.json')
        if not path.exists():raise ValueError('没有找到这次因子检验。')
        return json.loads(path.read_text(encoding='utf-8'))
    records=[]
    for path in ANALYSES.glob('*.json'):
        try:
            data=json.loads(path.read_text(encoding='utf-8'))
            record={k:data.get(k) for k in ('run_id','factor','label','generated_at','horizon','ic_mean','evaluated_days','coverage','params')}
            if query.get('q','').lower() in json.dumps(record,ensure_ascii=False).lower():records.append(record)
        except (ValueError,OSError):continue
    return dict(ok=True,analyses=sorted(records,key=lambda r:r.get('generated_at',''),reverse=True))


def experiment_detail(key,with_report=True):
    from backtest.research_runner import ARCHIVES
    if not re.fullmatch(r'research_[a-z0-9_]+_[0-9a-f]{32}',key):raise ValueError('回测编号无效。')
    path=ARCHIVES/(key+'.json')
    if not path.exists():raise ValueError('没有找到这次回测。')
    result = json.loads(path.read_text(encoding='utf-8'))
    if with_report:
        from backtest.research_report import build_report
        result['report'] = build_report(result)
    return result


def annotate(payload):
    key=payload.get('id','');experiment_detail(key)
    note=payload.get('note','')
    if not isinstance(note,str) or len(note)>2000:raise ValueError('备注最多 2000 字。')
    META.parent.mkdir(parents=True,exist_ok=True)
    with LOCK, closing(sqlite3.connect(META)) as con,con:
        con.execute('CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY,favorite INTEGER,note TEXT)')
        con.execute('INSERT INTO notes VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET favorite=excluded.favorite,note=excluded.note',
                    (key,int(bool(payload.get('favorite'))),note))
    return dict(ok=True,message='备注和收藏已保存。')
