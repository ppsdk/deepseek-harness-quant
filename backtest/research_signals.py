"""Fixed candidate lists with explicit user-defined entry/exit conditions."""
from contextlib import closing
from datetime import date,timedelta
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4
import numpy as np

DB=Path(__file__).resolve().parent.parent/'data/cache/research_signal_plans.db'


def plans(query=None):
    items=[]
    if DB.exists():
        with closing(sqlite3.connect(DB)) as con:items=[json.loads(r[0]) for r in con.execute('SELECT payload FROM plans ORDER BY rowid DESC')]
    key=(query or {}).get('id')
    if key:
        item=next((p for p in items if p['id']==key),None)
        if item is None:raise ValueError('没有找到这个信号方案。')
        return item
    return dict(ok=True,plans=items)


def save(payload):
    from backtest.research_pools import get_pool
    from factors.research_formula import compile_formula
    from factors.research_library import factor_definitions,read_config
    cfg=read_config('research_workflow')['signals'];pool=get_pool(payload.get('pool_id',''))
    if pool['count']>cfg['max_codes']:raise ValueError('观察名单最多'+str(cfg['max_codes'])+'只，请先缩小候选范围。')
    name=payload.get('name','')
    if not isinstance(name,str) or not name.strip() or len(name)>80:raise ValueError('请输入不超过80字的方案名称。')
    definitions=factor_definitions();entry=compile_formula(payload.get('entry',''),definitions);exit=compile_formula(payload.get('exit',''),definitions)
    wanted=set()
    def include(k):
        if k in wanted:return
        wanted.add(k)
        m=definitions.get(k,{})
        for dep in m.get('dependencies',m.get('recipe',{}).get('features',[])):include(dep)
    for k in entry['dependencies']+exit['dependencies']:include(k)
    item=dict(id=uuid4().hex,name=name.strip(),pool_id=pool['id'],pool_name=pool['name'],
              entry=entry,exit=exit,definitions={k:definitions[k] for k in wanted if k in definitions},
              pattern_configuration=read_config('research_patterns')['families'],
              created_at=date.today().isoformat())
    DB.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(DB)) as con,con:
        con.execute('CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        if con.execute('SELECT count(*) FROM plans').fetchone()[0]>=cfg['max_plans']:raise ValueError('保存的方案数量已达到配置上限。')
        con.execute('INSERT INTO plans VALUES(?,?)',(item['id'],json.dumps(item,ensure_ascii=False,allow_nan=False)))
    return dict(ok=True,**item,message='方案已保存。候选名单和规则固定；点击检查使用最新本地数据，不会自动下单。')


def prepare(p,cfg,definitions):
    if not p.get('signal_plan_id'):return definitions,None
    plan=plans({'id':p['signal_plan_id']})
    cfg['pattern_configuration']={**cfg.get('pattern_configuration',{}),**plan.get('pattern_configuration',{})}
    definitions={**definitions,**plan['definitions'],'signal_entry':dict(plan['entry'],label='入场条件'),
                 'signal_exit':dict(plan['exit'],label='退出条件')}
    return definitions,plan


def attach(panels,definitions,plan):
    if not plan:return
    from factors.research_library import raw_factor
    panels['_entry']=raw_factor('signal_entry',panels,definitions)
    panels['_exit']=raw_factor('signal_exit',panels,definitions)
    panels['_signal_plan']=plan


def check(payload):
    from backtest.research_runner import research_config,validate_params,load_panels
    from factors.research_library import read_config
    from backtest.research_workspace import _names
    from data.research_quality import local_calendar,closed_day
    cfg=research_config();plan=plans({'id':payload.get('id','')})
    end=payload.get('end') or closed_day()
    start=(date.fromisoformat(end)-timedelta(days=read_config('research_workflow')['signals']['default_chart_days'])).isoformat()
    from backtest.research_pools import get_pool
    pool=get_pool(plan['pool_id'])
    start=max(start,pool.get('signal_date') or start)
    if pool.get('signal_date') and end<pool['signal_date']:raise ValueError('检查日期不能早于候选名单确定日。')
    # Checks include the list's creation day; backtests separately require the following day.
    p=validate_params(dict(strategy=cfg['default_strategy'],codes=','.join(pool['codes']),stocks=pool['count'],
                   topn=min(pool['count'],cfg['parameters']['topn']['max']),start=start,end=end),cfg)
    p['signal_plan_id']=plan['id']
    panels,data=load_panels(p,cfg);names=_names();dates=panels['close'].index;calendar,origin=local_calendar(start,end)
    if not len(dates):raise ValueError('候选名单在此日期之前没有可读行情，请先更新数据或调整日期。')
    asof=calendar[-1] if calendar else dates[-1];rows=[];events=[];labels=read_config('research_workflow')['signals']['states']
    for code in pool['codes']:
        available=panels['close'][code].dropna();day=available.index[-1] if len(available) else None
        entry=panels['_entry'].at[day,code] if day else np.nan
        exit=panels['_exit'].at[day,code] if day else np.nan
        known=np.isfinite(entry) and np.isfinite(exit)
        state='stale' if day and day<asof else 'missing' if not known else 'conflict' if entry>0 and exit>0 else 'exit' if exit>0 else 'entry' if entry>0 else 'waiting'
        rows.append(dict(code=code,name=names.get(code,''),date=day,state=state,label=labels[state],entry=float(entry) if np.isfinite(entry) else None,exit=float(exit) if np.isfinite(exit) else None))
        for day in dates[dates>=start]:
            if pool.get('signal_date') and day<pool['signal_date']:continue
            a,b=panels['_entry'].at[day,code],panels['_exit'].at[day,code]
            if np.isfinite(a) and np.isfinite(b) and (a>0 or b>0):events.append(dict(code=code,date=day,entry=bool(a>0),exit=bool(b>0)))
    return dict(ok=True,plan=plan,start=start,asof=asof,calendar_source=origin,rows=rows,events=events,
                message='收盘条件为正表示触发；等待、数据不足和行情过旧分别显示。退出条件仅供已有持仓参考；信号不等于成交。')


def create_candidates(payload):
    """Reuse the shared strategy ranking; this does not create buy instructions."""
    from backtest.research_workspace import screen
    from backtest.research_pools import save_pool
    values=dict(payload);name=values.pop('pool_name','').strip()
    if not name:raise ValueError('请填写候选名单名称。')
    result=screen(values)
    if not result['candidates']:raise ValueError('本次没有满足策略和数据条件的股票，未保存空名单。')
    pool=save_pool(dict(name=name,codes=[r['code'] for r in result['candidates']],signal_date=result['signal_date'],source_strategy=result['strategy'],source_params=result['params']))
    return dict(ok=True,pool=pool,screen=result,message='已按共享策略评分保存 '+str(pool['count'])+' 只候选；名单确定日 '+pool['signal_date']+'。')


def signal_chart(query):
    from data.research_charts import chart_data
    plan=plans({'id':query.get('id','')})
    from backtest.research_pools import get_pool
    pool=get_pool(plan['pool_id']);code=query.get('code')
    if code not in pool['codes']:raise ValueError('请选择候选名单内的股票。')
    from factors.research_library import factor_definitions
    definitions={**factor_definitions(),**plan['definitions']}
    for key,label in [('entry','入场条件'),('exit','退出条件')]:definitions['signal_'+key]=dict(plan[key],label=label,chart=True,overlay=False)
    result=chart_data(code,code,dict(query,indicators='signal_entry,signal_exit',timeframe='day'),definitions=definitions,pattern_configuration=plan.get('pattern_configuration'))
    result['patterns']['kind']='user_conditions'
    result['patterns']['studies']=[dict(id=plan['id'],label=plan['name'],note='空心上/下标记是入场/退出条件触发，两个副图显示条件值；它们不代表实际成交。')]
    for bar in result['bars']:
        day=bar['date']
        if pool.get('signal_date') and day<pool['signal_date']:continue
        entry=result['indicators']['signal_entry']['values'].get(day);exit=result['indicators']['signal_exit']['values'].get(day)
        if entry is None or exit is None:continue
        for key,value,label,side in [('entry',entry,'入场条件','bullish'),('exit',exit,'退出条件','bearish')]:
            if value>0:result['patterns']['events'].append(dict(event='user_'+key,date=day,origin_date=day,bar_date=day,label=label,side=side,
                            detail=plan[key]['formula']+'；收盘确认，下一交易日才可尝试成交。'))
    return result
