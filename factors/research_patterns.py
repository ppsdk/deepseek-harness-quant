"""Causal chart studies and rule-based price/volume events shared by backtests.

Pivots are emitted on their confirmation bar. Higher periods are not available
until the period's calendar boundary has closed; incomplete bars never leak back.
"""
import json
import numpy as np
import pandas as pd


def config():
    from factors.research_library import read_config
    return read_config('research_patterns')


def holding(signal, valid, maximum):
    out = pd.DataFrame(np.nan, index=signal.index, columns=signal.columns)
    for col in signal:
        active, age = False, 0
        for i, (event, known) in enumerate(zip(signal[col], valid[col])):
            if not known or not np.isfinite(event):
                active, age = False, 0
                continue
            if event < 0:
                active, age = False, 0
            elif event > 0:
                active, age = True, 0
            elif active:
                age += 1
                if age >= maximum:
                    active = False
            out.iat[i, out.columns.get_loc(col)] = float(active)
    return out


def swings(high, low, left, right):
    """Strict extrema; origin=j is first knowable at j+right, never at j."""
    support = high * np.nan; resistance = high * np.nan; direction = high * np.nan
    events = []
    for col in high:
        highs, lows = [], []
        h, l = high[col].to_numpy(float), low[col].to_numpy(float)
        ci = high.columns.get_loc(col)
        for i in range(len(h)):
            if not np.isfinite(h[i]) or not np.isfinite(l[i]):
                highs, lows = [], []
                continue
            j = i - right
            if j >= left:
                hh, ll = h[j-left:i+1], l[j-left:i+1]
                for kind, window, records in [('high', hh, highs), ('low', ll, lows)]:
                    if not np.isfinite(window).all():
                        continue
                    center = window[left]; others = np.delete(window, left)
                    match = (center > others.max()) if kind == 'high' else (center < others.min())
                    if match:
                        records.append((j, center))
                        events.append(dict(code=col, origin=j, confirmed=i, kind=kind, price=float(center)))
            if highs:
                resistance.iat[i, ci] = highs[-1][1]
            if lows:
                support.iat[i, ci] = lows[-1][1]
            if len(highs) >= 2 and len(lows) >= 2:
                up = highs[-1][1] > highs[-2][1] and lows[-1][1] > lows[-2][1]
                down = highs[-1][1] < highs[-2][1] and lows[-1][1] < lows[-2][1]
                direction.iat[i, ci] = 1. if up else -1. if down else 0.
    return dict(support=support, resistance=resistance, direction=direction, pivots=events)


def _signal(buy, sell, valid):
    # An ambiguous day is treated conservatively: a sell condition wins.
    return buy.astype(float).mask(sell, -1.).where(valid)


def bundle(family, panels):
    memo = panels.setdefault('_pattern_cache', {})
    if family in memo:
        return memo[family]
    cfg = panels.get('_pattern_config',{}).get(family) or config()['families'][family]; a = cfg['params']
    c = panels['close'].astype(float)
    valid = c.notna() & c.gt(0)
    for key in cfg['requires']:
        valid &= panels[key].notna() & panels[key].gt(0)
    h, l = panels['high'].astype(float), panels['low'].astype(float)
    outputs, masks, events, segments = {}, {}, [], []
    from factors.research_indicators import calculate as technical
    if family == 'gaussian':
        n, sigma = a['window'], a['sigma']
        weights = np.exp(-.5 * (np.arange(n) / sigma) ** 2); weights /= weights.sum()
        center = c.rolling(n).apply(lambda values: np.dot(values, weights[::-1]), raw=True)
        atr = technical(dict(operation='atr', params={'n': a['atr_period']}), panels)
        slope = center.diff(a['slope_bars'])
        known = valid & center.notna() & slope.notna() & atr.notna()
        masks['cross_up'] = (c > center) & (c.shift() <= center.shift()) & slope.gt(0)
        masks['cross_down'] = ((c < center) & (c.shift() >= center.shift())) | (slope.lt(0) & slope.shift().ge(0))
        outputs.update(center=center, upper=center + atr * a['bands_atr'], lower=center - atr * a['bands_atr'],
                       trend=np.sign(slope).where(known))
        signal = _signal(masks['cross_up'], masks['cross_down'], known)
    elif family == 'wyckoff':
        v = panels['volume']; n = a['range_bars']
        support, resistance = l.rolling(n).min().shift(), h.rolling(n).max().shift()
        vr = v / v.rolling(a['volume_bars']).mean().shift().replace(0, np.nan)
        width = (resistance / support - 1) * 100
        position = (c-l) / (h-l).replace(0, np.nan)
        box = width.le(a['max_range_pct']); known = valid & support.notna() & vr.notna() & position.notna()
        spring = box & l.lt(support) & c.gt(support) & position.ge(a['reclaim_position']) & vr.ge(a['spring_volume_min'])
        upthrust = box & h.gt(resistance) & c.lt(resistance) & position.le(1-a['reclaim_position']) & vr.ge(a['spring_volume_min'])
        strength = box & c.gt(resistance * (1+a['break_buffer'])) & vr.ge(a['break_volume_min'])
        weakness = box & c.lt(support * (1-a['break_buffer'])) & vr.ge(a['break_volume_min'])
        # A retest uses the original earlier breakout level, not a later range.
        bull_level = resistance.where(strength).ffill(limit=a['retest_bars']).shift()
        bear_level = support.where(weakness).ffill(limit=a['retest_bars']).shift()
        lps = l.le(bull_level*(1+a['retest_tolerance'])) & c.gt(bull_level) & c.gt(panels['open']) & vr.le(a['retest_volume_max'])
        lpsy = h.ge(bear_level*(1-a['retest_tolerance'])) & c.lt(bear_level) & c.lt(panels['open']) & vr.le(a['retest_volume_max'])
        masks.update(spring=spring, strength=strength, last_support=lps, upthrust=upthrust, weakness=weakness, last_supply=lpsy)
        outputs.update(support=support, resistance=resistance, volume_ratio=vr,
                       spring=spring.astype(float).where(known), strength=strength.astype(float).where(known))
        signal = _signal(spring | strength | lps, upthrust | weakness | lpsy, known)
    elif family == 'dow':
        daily = swings(h, l, a['left'], a['right'])
        outputs.update(support=daily['support'], resistance=daily['resistance'], day=daily['direction'])
        index = pd.to_datetime(c.index)
        for key, rule in a['periods'].items():
            high = h.set_axis(index).resample(rule).max()
            low = l.set_axis(index).resample(rule).min()
            present = c.notna().set_axis(index).resample(rule).sum()
            high = high.where(present.ge(a['minimum_period_bars'][key]))
            low = low.where(present.ge(a['minimum_period_bars'][key]))
            higher = swings(high, low, a['left'], a['right'])['direction']
            # resample labels are the calendar period END, not its first day.
            mapped = higher.reindex(index, method='ffill'); mapped.index = c.index
            outputs[key] = mapped.where(valid)
        alignment = sum(outputs[key].fillna(0) for key in a['alignment_periods'])
        available = sum(outputs[key].notna().astype(int) for key in a['alignment_periods'])
        outputs['alignment'] = alignment.where(available >= a['minimum_known_periods'])
        outputs['known_periods'] = available.where(valid)
        known = valid & outputs['day'].notna()
        prior_high, prior_low = outputs['resistance'].shift(), outputs['support'].shift()
        masks['break_up'] = c.gt(prior_high) & c.shift().le(prior_high) & outputs['day'].eq(1)
        masks['break_down'] = c.lt(prior_low) & c.shift().ge(prior_low)
        signal = _signal(masks['break_up'], masks['break_down'] | outputs['day'].eq(-1), known)
        for pivot in daily['pivots']:
            event = cfg['events']['pivot_high' if pivot['kind'] == 'high' else 'pivot_low']
            events.append(dict(code=pivot['code'], date=str(c.index[pivot['confirmed']]),
                               origin_date=str(c.index[pivot['origin']]), label=event['label'],
                               detail=event['description'], side=event['side'], price=pivot['price'],
                               placement=event.get('placement'),
                               event='pivot_'+pivot['kind'], family=family))
    elif family == 'naked_k':
        o = panels['open']; spread = (h-l).replace(0, np.nan)
        body = (c-o).abs(); upper = h-pd.concat([o,c],keys=['o','c']).groupby(level=1).max().reindex_like(c)
        lower = pd.concat([o,c],keys=['o','c']).groupby(level=1).min().reindex_like(c)-l
        prior_down = c.shift().lt(c.shift(a['context_bars']))
        prior_up = c.shift().gt(c.shift(a['context_bars']))
        engulf_up = c.gt(o) & c.shift().lt(o.shift()) & o.le(c.shift()) & c.ge(o.shift()) & prior_down
        engulf_down = c.lt(o) & c.shift().gt(o.shift()) & o.ge(c.shift()) & c.le(o.shift()) & prior_up
        pin_up = (lower/spread).ge(a['wick_fraction']) & (body/spread).le(a['body_max_fraction']) & ((c-l)/spread).ge(a['close_position']) & prior_down
        pin_down = (upper/spread).ge(a['wick_fraction']) & (body/spread).le(a['body_max_fraction']) & ((h-c)/spread).ge(a['close_position']) & prior_up
        inside = h.shift().lt(h.shift(2)) & l.shift().gt(l.shift(2))
        inside_up = inside & c.gt(h.shift(2)); inside_down = inside & c.lt(l.shift(2))
        masks.update(engulf_up=engulf_up, engulf_down=engulf_down, pin_up=pin_up, pin_down=pin_down,
                     inside_up=inside_up, inside_down=inside_down)
        known = valid & valid.shift(fill_value=False) & c.shift(a['context_bars']).notna() & spread.notna()
        outputs.update(engulfing=_signal(engulf_up,engulf_down,known), pin_bar=_signal(pin_up,pin_down,known),
                       inside_break=_signal(inside_up,inside_down,known))
        signal = _signal(engulf_up | pin_up | inside_up, engulf_down | pin_down | inside_down, known)
    elif family == 'vpts':
        n=a['range_bars']; v=panels['volume']
        support=l.rolling(n).min().shift(); resistance=h.rolling(n).max().shift()
        vr=v/v.rolling(a['volume_bars']).mean().shift().replace(0,np.nan)
        width=(resistance/support-1)*100; tight=width.le(a['max_width_pct'])
        atr=technical(dict(operation='atr',params={'n':a['atr_period']}),panels)
        breakout=tight & c.gt(resistance*(1+a['break_buffer'])) & vr.ge(a['break_volume_min'])
        breakdown=tight & c.lt(support*(1-a['break_buffer'])) & vr.ge(a['break_volume_min'])
        level=resistance.where(breakout).ffill(limit=a['retest_bars']).shift()
        pullback=l.le(level*(1+a['retest_tolerance'])) & c.ge(level) & c.gt(panels['open']) & vr.le(a['retest_volume_max'])
        masks.update(breakout=breakout, breakdown=breakdown, pullback=pullback)
        known=valid & support.notna() & vr.notna() & atr.notna()
        counter=tight.astype(int).copy()
        for col in counter:
            counter[col]=tight[col].groupby((~tight[col]).cumsum()).cumsum()
        outputs.update(support=support,resistance=resistance,volume_ratio=vr,
                       support_distance=(c-support)/atr.replace(0,np.nan),
                       position=(c-support)/(resistance-support).replace(0,np.nan)*100,
                       tight_days=counter.astype(float).where(known))
        signal=_signal(breakout|pullback,breakdown,known)
    elif family == 'kdj':
        k=technical(dict(operation='kdj_k',params=a),panels); d=technical(dict(operation='kdj_d',params=a),panels)
        j=3*k-2*d
        masks['cross_up']=k.gt(d) & k.shift().le(d.shift()) & k.le(a['oversold']) & d.le(a['oversold'])
        masks['cross_down']=k.lt(d) & k.shift().ge(d.shift()) & k.ge(a['overbought']) & d.ge(a['overbought'])
        known=valid & k.notna() & d.notna() & d.shift().notna()
        outputs.update(k=k,d=d,j=j)
        signal=_signal(masks['cross_up'],masks['cross_down'],known)
    elif family == 'rsi_divergence':
        rsi=technical(dict(operation='rsi',params={'n':a['rsi_period']}),panels)
        pivot=swings(h,l,a['left'],a['right']); known=valid & rsi.notna()
        signal=c*0.; signal=signal.where(known)
        previous={}
        for p in pivot['pivots']:
            col,j,i,kind=p['code'],p['origin'],p['confirmed'],p['kind']; old=previous.get((col,kind))
            osc=rsi[col].iloc[j]
            if not np.isfinite(osc):continue
            if old:
                oj,price,old_osc=old;gap=j-oj
                contiguous=valid[col].iloc[oj:i+1].all() and rsi[col].iloc[oj:i+1].notna().all()
                if a['min_separation']<=gap<=a['max_separation'] and contiguous:
                    bull=kind=='low' and p['price']<price*(1-a['price_change_min']) and osc>=old_osc+a['rsi_change_min'] and osc<=a['bull_rsi_max']
                    bear=kind=='high' and p['price']>price*(1+a['price_change_min']) and osc<=old_osc-a['rsi_change_min'] and osc>=a['bear_rsi_min']
                    if bull or bear:
                        event_id='bullish' if bull else 'bearish'; spec=cfg['events'][event_id]
                        signal.at[c.index[i],col]=1. if bull else -1.
                        events.append(dict(code=col,date=str(c.index[i]),origin_date=str(c.index[j]),
                                           first_date=str(c.index[oj]),label=spec['label'],detail=spec['description'],
                                           side=spec['side'],event=event_id,family=family,
                                           price=p['price'],rsi=float(osc),previous_rsi=float(old_osc)))
                        segments.append(dict(code=col,confirmed_date=str(c.index[i]),side=spec['side'],
                                             start_date=str(c.index[oj]),end_date=str(c.index[j]),
                                             start_price=float(price),end_price=p['price'],
                                             start_osc=float(old_osc),end_osc=float(osc),osc_output='rsi'))
            previous[(col,kind)]=(j,p['price'],float(osc))
        outputs['rsi']=rsi
    else:
        raise ValueError('尚未实现的形态算法：'+family)
    outputs['signal']=signal.where(valid)
    outputs['state']=holding(signal,known,a['max_hold'])
    for event_id,mask in masks.items():
        if event_id not in cfg['events']:continue
        spec=cfg['events'][event_id]
        for i,j in np.argwhere((mask & known).fillna(False).to_numpy(bool)):
            event=dict(code=str(c.columns[j]),date=str(c.index[i]),origin_date=str(c.index[i]),
                       label=spec['label'],detail=spec['description'],side=spec['side'],
                       event=event_id,family=family,price=float(c.iat[i,j]))
            for key in ('volume_ratio','support','resistance'):
                value=outputs.get(key)
                if value is not None and pd.notna(value.iat[i,j]):event[key]=float(value.iat[i,j])
            events.append(event)
    for key,value in outputs.items():outputs[key]=value.replace([np.inf,-np.inf],np.nan).where(valid)
    result=dict(outputs=outputs,events=events,segments=segments)
    memo[family]=result
    return result


def calculate(spec,panels):
    return bundle(spec['family'],panels)['outputs'][spec['output']]


def chart_study(spec,panels,code,bar_dates,start,end):
    """One selected study can expose several compatible lines and event markers."""
    from bisect import bisect_left
    family=spec['family']; cfg=panels.get('_pattern_config',{}).get(family) or config()['families'][family]; result=bundle(family,panels)
    lines={}
    for key,line in cfg['visual']['lines'].items():
        values=result['outputs'][key].reindex(bar_dates)[code]
        lines[family+':'+key]=dict(line,values=json.loads(values.to_json()))
    events=[]
    for event in result['events']:
        if event['code']!=code or not start<=event['date']<=end:continue
        i=bisect_left(bar_dates,event['date'])
        if i<len(bar_dates):events.append(dict(event,bar_date=bar_dates[i]))
    segments=[]
    if cfg['visual'].get('segments'):
        for segment in result['segments']:
            if segment['code']==code and start<=segment['confirmed_date']<=end:
                segments.append(dict(segment,osc_panel=cfg['visual'].get('osc_panel')))
    return dict(lines=lines,events=events,segments=segments,note=cfg['note'])
