"""Causal, optional stock selection from ranked candidates and actual holdings.

Thresholds and periods are supplied by the caller's configuration. Insufficient
correlation history leaves a slot vacant rather than fabricating diversification.
"""
import math


def choose(panels, ranked, holdings, signal_day, topn, policy, blocked=()):
    blocked=set(blocked)
    available=[code for code in ranked.index if code not in blocked]
    retain=int(policy.get('retain_rank',0))
    retained=[code for code in available[:retain] if code in holdings] if retain else []

    # Give an existing holding a configurable score advantage when comparing
    # it with challengers. This avoids replacing a name for a negligible rank
    # change while still dropping stale names outside the candidate pool.
    margin=max(0.,float(policy.get('replacement_score_margin',0.)))
    if margin and available:
        adjusted=ranked.loc[available].copy()
        for code in holdings:
            if code in adjusted.index:
                adjusted.at[code]+=margin
        challengers=[code for code in adjusted.sort_values(
            ascending=False,kind='stable').index if code not in retained]
        ordered=retained+challengers
    else:
        ordered=retained+[code for code in available if code not in retained]
    limit=policy.get('correlation_limit')
    if limit is None:return ordered[:topn]
    ordered=ordered[:int(policy['max_candidates'])]
    history=panels['close'].loc[:signal_day,ordered].tail(int(policy['correlation_window'])+1)
    returns=history.pct_change(fill_method=None)
    minimum=int(policy['correlation_min_observations'])
    chosen=[]
    for code in ordered:
        if returns[code].notna().sum()<minimum:continue
        acceptable=True
        for previous in chosen:
            pair=returns[[code,previous]].dropna()
            if len(pair)<minimum:
                acceptable=False;break
            corr=float(pair.iloc[:,0].corr(pair.iloc[:,1]))
            if not math.isfinite(corr) or corr>float(limit):
                acceptable=False;break
        if acceptable:chosen.append(code)
        if len(chosen)==topn:break
    return chosen
