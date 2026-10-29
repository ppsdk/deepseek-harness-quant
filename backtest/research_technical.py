"""Config-driven technical candidates; signals use observed closes only."""
import numpy as np


def candidate(panels, eligible, risk_mask, spec, factor_loader=None):
    close=panels['close']
    n=int(spec['confirm_days'])
    if spec['average']=='ema':
        fast=close.ewm(span=int(spec['fast']),adjust=False,min_periods=int(spec['fast'])).mean()
        slow=close.ewm(span=int(spec['slow']),adjust=False,min_periods=int(spec['slow'])).mean()
    else:
        fast=close.rolling(int(spec['fast'])).mean()
        slow=close.rolling(int(spec['slow'])).mean()
    known=close.notna()&fast.notna()&slow.notna()
    valid=eligible&risk_mask
    momenta=[]
    skip=int(spec.get('skip_days',0))
    for period in spec['momentum_periods']:
        momenta.append(close.shift(skip)/close.shift(skip+int(period))-1)
    # A fresh absolute momentum check prevents a stale skip-window rally
    # from permitting new buys after the current trend has broken.
    absolute=close/close.shift(int(spec['absolute_period']))-1
    trend=fast.gt(slow)&close.gt(fast)
    if spec['operation']=='dual_momentum':
        enter=absolute.gt(0)&close.gt(slow)
        for m in momenta:enter &= m.gt(0)
        leave=absolute.le(0)|close.lt(slow)
    elif spec['operation']=='ma_trend':
        enter=trend
        leave=fast.lt(slow)
    elif spec['operation']=='ma_pullback':
        ratio=close/slow-1
        enter=fast.ge(slow)&ratio.ge(spec['min_price_vs_slow'])&ratio.le(spec['max_price_vs_slow'])
        enter &= (slow/slow.shift(int(spec['slope_days']))-1).ge(spec['min_slope'])
        leave=close.lt(slow)
    else:raise ValueError('不支持的技术研究规则。')
    entry=enter.rolling(n).sum().eq(n).astype(float).where(known)
    exit_signal=leave.rolling(n).sum().eq(n).astype(float).where(known)
    ranks=[m.where(valid).rank(axis=1,pct=True) for m in momenta]
    if spec['score']=='momentum':
        score=sum(ranks)/len(ranks)
    elif spec['score']=='defensive':
        if factor_loader is None:raise ValueError('防御排序需要因子缓存。')
        ranks=[(-factor_loader(name)).where(valid).rank(axis=1,pct=True) for name in spec['score_factors']]
        score=sum(ranks)/len(ranks)
    else:raise ValueError('不支持的技术研究排序。')
    score=score.where(valid&known&np.isfinite(score))
    if spec['mode']=='fixed':
        score=score.where(entry.eq(1))
        signals={'_exit':exit_signal}
    else:
        signals={'_entry':entry.where(valid), '_exit':exit_signal}
    return score,signals
