"""Causal index gates, daily risk exits and comparisons on one price snapshot."""
from datetime import date,timedelta
import time
import numpy as np
import pandas as pd


def evaluate(frame,key,cfg,seen=None):
    seen=set() if seen is None else set(seen)
    if key in seen:raise ValueError('市场择时规则存在循环引用。')
    seen.add(key)
    rule=cfg['rules'][key];c=frame['close'].astype(float);op=rule.get('operation')
    if key=='none':return pd.Series(1.,index=c.index)
    if op=='all':
        parts=pd.concat([evaluate(frame,k,cfg,seen) for k in rule['rules']],axis=1)
        return parts.eq(1).all(axis=1).astype(float).where(parts.notna().all(axis=1))
    if op=='above_ma':a,b=c,c.rolling(rule['period']).mean()
    elif op=='ma_cross':a,b=c.rolling(rule['fast']).mean(),c.rolling(rule['slow']).mean()
    elif op=='macd_positive':
        a=c.ewm(span=rule['fast'],adjust=False,min_periods=rule['fast']).mean()-c.ewm(span=rule['slow'],adjust=False,min_periods=rule['slow']).mean();b=pd.Series(0.,index=c.index)
    elif op=='rsi_above':
        from factors.research_indicators import calculate
        a=calculate({'operation':'rsi','params':{'n':rule['period']}},{'close':c.to_frame('index')})['index'];b=pd.Series(rule['threshold'],index=c.index)
        return a.ge(b).astype(float).where(a.notna()&c.notna())
    elif op=='volatility_below':
        v=c.pct_change(fill_method=None).rolling(rule['period']).std(ddof=1)*np.sqrt(rule['annual_periods'])
        return v.le(rule['threshold']).astype(float).where(v.notna()&c.notna())
    elif op=='drawdown_above':
        v=c/c.rolling(rule['period']).max()-1
        return v.ge(rule['threshold']).astype(float).where(v.notna()&c.notna())
    elif op=='rsrs':
        n,m=rule['period'],rule['standardize_period'];low,high=frame['low'],frame['high']
        beta=low.rolling(n).cov(high)/low.rolling(n).var().replace(0,np.nan)
        score=(beta-beta.rolling(m).mean())/beta.rolling(m).std().replace(0,np.nan)*low.rolling(n).corr(high).pow(2)
        values=[];state=0.
        for v in score:
            if pd.isna(v):values.append(np.nan);continue
            if v>=rule['entry']:state=1.
            elif v<=rule['exit']:state=0.
            values.append(state)
        return pd.Series(values,index=c.index)
    else:raise ValueError('未安装的市场择时算法。')
    return a.gt(b).astype(float).where(a.notna()&b.notna()&c.notna())


def index_history(cache,code,start,end,key,cfg):
    rule=cfg['rules'][key]
    # Empty start is supported by existing internal callers that request the whole cache.
    warmup=(date.fromisoformat(start)-timedelta(days=rule.get('warmup_calendar_days',400))).isoformat() if start else None
    df=cache.get_daily(code,start=warmup,end=end or None,adjust='none')
    if df is None or df.empty:raise ValueError('缺少所选指数日线，请先更新该指数。')
    return df.sort_values('date').set_index('date')


def attach_timing(panels,p,cache,start,end):
    key=p.get('timing_rule','none')
    if key=='none':return
    from data.research_services import settings
    services=settings();cfg=services['timing'];code=p.get('timing_index',cfg['benchmark']['code'])
    frame=index_history(cache,code,start,end,key,cfg)
    gate=evaluate(frame,key,cfg).reindex(panels['close'].index)
    panels['_timing']=pd.DataFrame({c:gate for c in panels['close'].columns})


def compare(values):
    from backtest.research_runner import research_config,validate_params,load_panels,factor_score,build_result
    started=time.monotonic();cfg=research_config();p=validate_params(values,cfg)
    if p['timing_rule']=='none':raise ValueError('请选择一条择时规则，再与不择时的结果比较。')
    panels,data=load_panels(p,cfg);meta=cfg['strategies'][p['strategy']]
    plain={k:v for k,v in panels.items() if k!='_timing'};base_p=dict(p,timing_rule='none')
    raw=factor_score(plain,meta['factor_list']);timed=raw.where(panels['_timing'].eq(1))
    baseline=build_result(plain,data,raw,base_p,cfg,meta,started)
    filtered=build_result(panels,data,timed,p,cfg,meta,started)
    return dict(ok=True,baseline=baseline,timed=filtered,
                message='两次回测使用同一份股票行情与策略，只改变市场择时条件；含佣金、税和滑点。')
