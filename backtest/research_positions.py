"""Read archived holdings and budget a user-selected list; no live orders or portfolio writes."""
from datetime import date,timedelta
import re
import pandas as pd


def overview(query):
    from factors.research_manager import experiment_list,experiment_detail
    runs=experiment_list({})['runs']
    key=query.get('run') or (runs[0]['id'] if runs else None)
    if not key:
        return dict(ok=True,state='empty',runs=runs,positions=[],message='还没有回测。先运行一次策略回测，再查看它的期末仓位。')
    result=experiment_detail(key,with_report=False)
    available='position_details' in result
    positions=result.get('position_details',[])
    if not available:
        positions=[dict(code=c,quantity=q) for c,q in result.get('final_positions',{}).items()]
    accounts=result.get('daily_account',[])
    return dict(ok=True,state='ready',runs=runs,run=key,title=result.get('title',key),
                date=(result.get('dates') or [None])[-1],positions=positions,
                detail_available=available,account=accounts[-1] if accounts else None,
                realized_pnl=sum(result.get('realized_pnl',{}).values()) if 'realized_pnl' in result else None,
                message='来自所选实验的期末快照，价格和仓位截至回测结束日。' if available else
                        '旧实验仅保存了调整份额，缺少逐股成本和期末估值；对应金额和盈亏留空。新运行的实验会保存完整快照。')


def plan(payload):
    from backtest.research_runner import research_config,validate_params
    from backtest.research_portfolio import weights
    from backtest.research_pools import get_pool
    from data.cache import DailyCache
    codes=payload.get('codes',[])
    pool=None
    if payload.get('pool_id'):
        pool=get_pool(payload['pool_id'])
        codes=pool['codes']
    if isinstance(codes,str):
        codes=re.split(r'[,\s]+',codes.strip())
    if not isinstance(codes,list) or any(not isinstance(c,str) for c in codes):
        raise ValueError('请提供股票池，或填写完整股票代码。')
    codes=list(dict.fromkeys(c.strip().upper() for c in codes if c.strip()))
    if not 1<=len(codes)<=200 or any(not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',c) for c in codes):
        raise ValueError('请提供1至200个完整股票代码。')
    cfg=research_config()
    values={k:v for k,v in payload.items() if k in cfg['parameters'] and k not in ('pool_id','codes','strategy')}
    values['start']=values.get('end') or cfg['parameters']['end']['default']
    values.update(strategy=cfg['default_strategy'],topn=len(codes),stocks=len(codes),codes=','.join(codes))
    params=validate_params(values,cfg)
    asof=date.fromisoformat(params['end'])
    if asof>date.today():raise ValueError('仓位预算日期不能晚于今天。')
    if pool and pool.get('signal_date') and params['end']<pool['signal_date']:
        raise ValueError('预算日期早于股票池选出日期，不能把后来才选出的名单用于过去。')
    history=DailyCache().get_daily_batch(codes,start=(asof-timedelta(days=params['allocation_lookback']*3+20)).isoformat(),end=asof.isoformat(),adjust='qfq',fields=['close'])
    if not history:raise ValueError('这些股票没有已下载行情，请先更新数据。')
    closes=pd.DataFrame({c:f.set_index('date')['close'] for c,f in history.items()}).sort_index()
    signal=closes.index[-1]
    available=[c for c in codes if c in closes and pd.notna(closes.at[signal,c]) and closes.at[signal,c]>0]
    allocation=weights({'close':closes},available,signal,params)
    rows=[dict(code=c,weight=w,budget=w*params['initial_capital'],reference_close=float(closes.at[signal,c])) for c,w in allocation.items()]
    return dict(ok=True,date=signal,requested_date=params['end'],capital=params['initial_capital'],positions=rows,
                cash_budget=params['initial_capital']*(1-sum(allocation.values())),
                excluded_codes=[c for c in codes if c not in allocation],params=params,
                message='只对你提供的名单分配预算，未生成股票推荐或委托。参考价为本地前复权收盘价，预算未扣交易费用；真实数量需要原始价格、整手及次日开盘另行确认。')
