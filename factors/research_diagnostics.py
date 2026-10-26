"""Per-date factor cleaning and diagnostics without full-period fitted statistics."""
import numpy as np
import pandas as pd


def clean(raw,mode,policy):
    if mode not in policy['preprocessing']:raise ValueError('请选择支持的因子预处理。')
    if mode=='none':return raw
    lo,hi=policy['winsor_quantiles'];out=raw.clip(lower=raw.quantile(lo,axis=1),upper=raw.quantile(hi,axis=1),axis=0)
    if mode=='daily_winsor_z':out=out.sub(out.mean(axis=1),axis=0).div(out.std(axis=1).replace(0,np.nan),axis=0)
    return out


def future_returns(panels,h):
    opening=panels['open'];future=opening.shift(-(h+1))/opening.shift(-1)-1
    mask=(panels['volume'].shift(-1)>0)&(panels['volume'].shift(-(h+1))>0)
    mask&=(panels['high'].shift(-1)>panels['low'].shift(-1))&(panels['high'].shift(-(h+1))>panels['low'].shift(-(h+1)))
    mask&=(opening.shift(-1)>0)&(opening.shift(-(h+1))>0)
    return future.replace([np.inf,-np.inf],np.nan).where(mask&panels['is_st'].ne(1))


def diagnostics(raw,panels,start,end,policy):
    ranked=raw.rank(axis=1,pct=True);autocorrelation=[];decay=[]
    for lag in policy['autocorrelation_lags']:
        previous=raw.shift(lag);valid=raw.notna()&previous.notna()
        values=raw.where(valid).rank(axis=1).corrwith(previous.where(valid).rank(axis=1),axis=1)
        values=values.loc[start:end].dropna()
        autocorrelation.append(dict(lag=lag,mean=float(values.mean()) if len(values) else None,days=len(values)))
    for h in policy['horizons']:
        future=future_returns(panels,h);valid=raw.notna()&future.notna()
        values=raw.where(valid).rank(axis=1).corrwith(future.where(valid).rank(axis=1),axis=1).where(valid.sum(axis=1)>=policy['min_stocks'])
        values=values.loc[start:end].dropna()
        decay.append(dict(horizon=h,ic_mean=float(values.mean()) if len(values) else None,days=len(values)))
    return dict(autocorrelation=autocorrelation,ic_decay=decay)
