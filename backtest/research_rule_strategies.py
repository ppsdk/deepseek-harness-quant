"""Transparent entry/exit rules, without factor selection or learned scores."""
from functools import lru_cache
import json
from pathlib import Path

import numpy as np
import pandas as pd

from factors.research_indicators import calculate
from factors.research_library import read_config

ROOT=Path(__file__).resolve().parents[1]


def confirmed(condition, known, days):
    values=condition.astype(float).where(known)
    return values.rolling(int(days),min_periods=int(days)).sum().eq(int(days)).astype(float).where(known)


def fundamental_mask(valuations, eligible, spec):
    mask=eligible.copy()
    for field,limits in spec['limits'].items():
        value=valuations[field]
        mask &= np.isfinite(value)
        if 'min_exclusive' in limits:mask &= value.gt(limits['min_exclusive'])
        if 'max_inclusive' in limits:mask &= value.le(limits['max_inclusive'])
    return mask


def components(panels, settings):
    def indicator(op,**params):return calculate(dict(operation=op,params=params),panels)
    n=settings['kdj_period'];smooth=settings['kdj_smooth'];dmi=settings['dmi_period']
    return dict(k=indicator('kdj_k',n=n,smooth=smooth),d=indicator('kdj_d',n=n,smooth=smooth),
        pdi=indicator('dmi_plus',n=dmi),mdi=indicator('dmi_minus',n=dmi),adx=indicator('adx',n=dmi),
        average_amount=panels['amount'].rolling(settings['queue_liquidity_window']).mean())


def signals(panels, common, eligible, fundamental, spec, settings, queue='liquidity'):
    close=panels['close']
    fast=calculate(dict(operation=spec['average'],params={'n':spec['fast']}),panels)
    slow=calculate(dict(operation=spec['average'],params={'n':spec['slow']}),panels)
    known=close.notna()&fast.notna()&slow.notna()
    trend=fast.gt(slow)&close.gt(fast)
    entry=confirmed(trend,known,settings['trend_confirm']).eq(1)&(close/fast-1).le(settings['max_price_vs_fast'])
    leave=fast.lt(slow)|close.lt(slow)
    if spec['kdj']:
        k,d=common['k'],common['d']
        kknown=k.notna()&d.notna()&k.shift().notna()&d.shift().notna()
        cross=k.gt(d)&k.shift().le(d.shift())&k.le(settings['kdj_entry_max'])
        recent=cross.astype(float).where(kknown).rolling(settings['kdj_cross_lookback']).max().eq(1)
        entry &= recent&k.gt(d)
        leave |= k.lt(d)&k.shift().ge(d.shift())&k.ge(settings['kdj_exit_min'])
        known &= kknown
    if spec['dmi']:
        pdi,mdi,adx=common['pdi'],common['mdi'],common['adx']
        known &= pdi.notna()&mdi.notna()&adx.notna()
        entry &= pdi.gt(mdi)&adx.ge(settings['adx_min'])
        leave |= pdi.lt(mdi)
    enter=entry.astype(float).where(known&eligible&fundamental)
    # Rare overbought KDJ reversal is an event; don't erase it by requiring it
    # to repeat on consecutive days. The structural trend exit is confirmed.
    structural=fast.lt(slow)|close.lt(slow)
    if spec['dmi']:structural |= common['pdi'].lt(common['mdi'])
    exit_signal=confirmed(structural,known,settings['exit_confirm'])
    if spec['kdj']:
        exit_signal=exit_signal.mask(leave&~structural&known,1.)
    if queue=='liquidity':priority=common['average_amount']
    elif queue=='code_order':
        priority=pd.DataFrame(np.broadcast_to(np.arange(len(close.columns),0,-1),close.shape),index=close.index,columns=close.columns)
    else:raise ValueError('Unknown execution queue')
    # This numeric adapter is solely a deterministic order queue for the shared
    # ledger; no alpha factors, rank normalization or model scores are fitted.
    priority=priority.where(eligible&fundamental&known)
    return priority,dict(_entry=enter,_exit=exit_signal),dict(fast=fast,slow=slow)


def market_gate(frame, spec):
    close=frame['close'];fast=close.rolling(spec['fast']).mean();slow=close.rolling(spec['slow']).mean()
    known=close.notna()&fast.notna()&slow.notna()
    return confirmed(close.gt(slow)&fast.gt(slow),known,spec['confirm_days']),dict(close=close,fast=fast,slow=slow)


@lru_cache(maxsize=8)
def _read(path,mtime,size):return json.loads(Path(path).read_text(encoding='utf-8'))


def catalog():
    cfg=read_config('technical_rule_research');path=ROOT/cfg['output_dir']/'platform_catalog.json'
    if not path.exists():return dict(ok=True,state='pending',title=cfg['title'],entries=[],note=cfg['design_note'])
    stat=path.stat()
    return _read(str(path),stat.st_mtime_ns,stat.st_size)
