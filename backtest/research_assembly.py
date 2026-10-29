"""Causal, configuration-owned factor blends and moving-average trade signals."""
import numpy as np


def blend(raw,records,weights,eligible):
    if not weights or any(isinstance(v,bool) or not np.isfinite(v) or v<=0 for v in weights.values()):raise ValueError('Strategy weights must be positive and finite')
    ranks=[]
    for slot,weight in weights.items():
        ranks.append((raw[slot]*records[slot]['direction']).where(eligible).rank(axis=1,pct=True)*weight)
    return sum(ranks)/sum(weights.values())


def signals(panels,variant):
    if variant['signal']=='none':return {}
    c=panels['close'];fast=c.rolling(variant['fast']).mean();slow=c.rolling(variant['slow']).mean()
    exit_ma=c.rolling(variant['exit_period']).mean();known=c.notna()&fast.notna()&slow.notna()&exit_ma.notna()
    if variant['signal']=='trend':enter=c.gt(fast)&fast.gt(slow)
    elif variant['signal']=='pullback':
        slope=slow/slow.shift(variant['slope_days'])-1;ratio=c/fast-1
        enter=fast.ge(slow)&ratio.ge(variant['min_price_vs_fast'])&ratio.le(variant['max_price_vs_fast'])&slope.ge(variant['min_slow_slope'])
        known &= slope.notna()
    else:raise ValueError('Unknown assembly signal')
    n=variant['confirm_days']
    return {'_entry':enter.rolling(n).sum().eq(n).astype(float).where(known),
            '_exit':c.lt(exit_ma).rolling(n).sum().eq(n).astype(float).where(known)}


def stable_shortlist(score,core,direction,eligible,count):
    if isinstance(count,bool) or not isinstance(count,int) or count<1:raise ValueError('Candidate pool size must be a positive integer')
    rank=(core*direction).where(eligible).rank(axis=1,method='first',ascending=False)
    return score.where(rank.le(count))


def choose_complements(core,candidates,correlation,categories,limit,maximum):
    if maximum<=0:return []
    chosen=[]
    for category in categories:
        available=sorted([r for r in candidates if r['category']==category and r['id']!=core['id'] and r['family_id']!=core['family_id']],
                         key=lambda r:(r['robust_score'],abs(r['rank_ic'])),reverse=True)
        for record in available:
            values=correlation.loc[record['id'],[core['id'],*[r['id'] for r in chosen]]]
            if values.notna().all() and values.abs().le(limit).all():
                chosen.append(record);break
        if len(chosen)>=maximum:break
    return chosen


def joint_ranking(base,pressure):
    viable=all(r['checks']['exposure'] and r['checks']['activity'] and r['checks']['positive_return'] for r in [base,pressure])
    return (base['constraints_pass'] and pressure['constraints_pass'],viable,
            min(base['metrics']['annual_return'],pressure['metrics']['annual_return']),
            min(base['metrics']['max_drawdown'],pressure['metrics']['max_drawdown']),
            -max(base['annual_one_way_turnover'],pressure['annual_one_way_turnover']))
