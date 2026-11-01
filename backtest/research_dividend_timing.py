"""Simple, causal buffered-trend signals on dividend-adjusted close prices."""
import numpy as np


def signals(panels, slow, band, confirm_days):
    if isinstance(slow, bool) or not isinstance(slow, int) or slow < 2:
        raise ValueError('slow 必须为至少2的整数。')
    if not isinstance(band, (int, float)) or not np.isfinite(band) or not 0 <= band < 1:
        raise ValueError('band 必须在[0,1)内。')
    if isinstance(confirm_days, bool) or not isinstance(confirm_days, int) or confirm_days < 1:
        raise ValueError('confirm_days 必须为正整数。')
    from backtest.research_rule_strategies import confirmed
    close = panels['close']
    average = close.rolling(slow, min_periods=slow).mean()
    known = close.notna() & average.notna()
    entry = confirmed(close.gt(average*(1+band)), known, confirm_days)
    leave = confirmed(close.lt(average*(1-band)), known, confirm_days)
    return dict(_entry=entry, _exit=leave), average


def volatility_budget(panels, window, target_volatility, floor, annual_days):
    """Trailing realized-volatility allocation; the ledger executes next open."""
    if (isinstance(window, bool) or not isinstance(window, int) or window < 2
            or not np.isfinite(target_volatility) or not 0 < target_volatility < 1
            or not 0 <= floor <= 1 or annual_days <= 0):
        raise ValueError('波动预算参数无效。')
    close=panels['close']
    if close.shape[1]!=1:
        raise ValueError('单资产波动预算只能输入一个标的。')
    vol=close.pct_change(fill_method=None).rolling(window,min_periods=window).std(ddof=1)*np.sqrt(annual_days)
    allocation=(target_volatility/vol.replace(0,np.nan)).clip(lower=floor,upper=1.)
    # A known flat price window has zero estimated risk, not unknown risk.
    allocation=allocation.mask(vol.eq(0),1.)
    return allocation.iloc[:,0]*100.,vol.iloc[:,0]


def technical_overlay(panels, benchmark, group, variant, settings):
    """Apply optional confirmation layers to a volatility target.

    The overlay only caps exposure; it never creates leverage or a second
    execution path.  All conditions use the prior close because the shared
    portfolio ledger executes the resulting target at the next open.
    """
    target = panels['_base_target_exposure_pct'].astype(float).copy()
    cap = 100.0
    diagnostics = {}
    if variant in ('market_gate', 'combo'):
        if benchmark is None:
            raise ValueError('市场门控需要沪深300行情。')
        close = benchmark['close'].astype(float)
        if getattr(close, 'ndim', 1) == 2:
            close = close.iloc[:, 0]
        fast = close.rolling(int(settings['market_fast']), min_periods=int(settings['market_fast'])).mean()
        slow = close.rolling(int(settings['market_slow']), min_periods=int(settings['market_slow'])).mean()
        from backtest.research_rule_strategies import confirmed
        known = close.notna() & fast.notna() & slow.notna()
        gate = confirmed(close.gt(slow) & fast.gt(slow), known, int(settings['market_confirm_days'])).eq(1)
        # Keep the nullable boolean conversion explicit. This avoids pandas'
        # object-dtype downcast warning while preserving the conservative
        # pre-warmup behavior (unknown market state => gate closed).
        gate = gate.reindex(target.index).astype('boolean').fillna(False).astype(bool)
        market_cap = float(settings['market_caps'][group]) * 100.0
        target = target.where(gate, target.clip(upper=market_cap))
        diagnostics['market_gate_pass_rate'] = float(gate.mean()) if gate.notna().any() else None
        cap = min(cap, market_cap)
    close = panels['close'].astype(float).iloc[:, 0]
    if variant in ('bollinger', 'combo'):
        n = int(settings['bollinger_period']); k = float(settings['bollinger_k'])
        mid = close.rolling(n, min_periods=n).mean(); sd = close.rolling(n, min_periods=n).std(ddof=0)
        position = (close - (mid-k*sd)) / (2*k*sd).replace(0, np.nan)
        overbought = position.ge(float(settings['bollinger_overbought_position']))
        target = target.where(~overbought, target.clip(upper=float(settings['bollinger_cap']) * 100.0))
        diagnostics['bollinger_overbought_days'] = int(overbought.astype('boolean').fillna(False).sum())
        cap = min(cap, float(settings['bollinger_cap']) * 100.0)
    if variant in ('kdj', 'combo'):
        n = int(settings['kdj_period']); smooth = int(settings['kdj_smooth'])
        high = panels['high'].astype(float).iloc[:, 0].rolling(n, min_periods=n).max()
        low = panels['low'].astype(float).iloc[:, 0].rolling(n, min_periods=n).min()
        rsv = (close-low) / (high-low).replace(0, np.nan) * 100.0
        kval = rsv.ewm(alpha=1/smooth, adjust=False, min_periods=smooth).mean()
        dval = kval.ewm(alpha=1/smooth, adjust=False, min_periods=smooth).mean()
        overbought = kval.ge(float(settings['kdj_overbought'])) & kval.lt(dval)
        target = target.where(~overbought, target.clip(upper=float(settings['kdj_cap']) * 100.0))
        diagnostics['kdj_overbought_days'] = int(overbought.astype('boolean').fillna(False).sum())
        cap = min(cap, float(settings['kdj_cap']) * 100.0)
    return target.clip(lower=0., upper=100.), diagnostics


def causal_signal_panels(panels, actions):
    """Forward-only total-return OHLC for signals; keep raw execution separate.

    Actions are applied on the first available bar on/after ex-date. No future
    action rescales earlier prices. Cash is a gross distribution signal proxy,
    while the shared execution ledger handles actual payment and FIFO tax.
    """
    import pandas as pd
    view = dict(panels)
    for field in ('open', 'high', 'low', 'close'):
        view[field] = panels[field].astype(float).copy()
    for code in panels['close']:
        close = panels['close'][code].astype(float)
        applicable = sorted([e for e in actions if e['code'] == code], key=lambda e: e['ex_date'])
        scale = pd.Series(np.nan, index=close.index)
        previous = None
        level = None
        cursor = 0
        for day, value in close.items():
            if not np.isfinite(value) or value <= 0:
                continue
            ratio, cash = 1., 0.
            while cursor < len(applicable) and applicable[cursor]['ex_date'] <= str(day)[:10]:
                event = applicable[cursor]
                if event['available_date'] > event['ex_date']:
                    raise ValueError('信号公司行为在除权时尚不可用。')
                cash += ratio * float(event['cash_per_share'])
                ratio *= float(event['split_ratio'])
                cursor += 1
            level = value if previous is None else level * (value * ratio + cash) / previous
            scale.loc[day] = level / value
            previous = value
        for field in ('open', 'high', 'low', 'close'):
            view[field][code] = view[field][code] * scale
    return view


def reversal_signals(panels, variant, settings):
    """Low-zone reversal entry, high-zone/trend failure exit, no pivot backdating."""
    if variant == 'kdj_regime':
        from backtest.research_kdj_regime import signals as regime_signals
        return regime_signals(panels, settings)
    if variant == 'kdj_trend_exit':
        from backtest.research_kdj_trend_exit import signals as trend_exit_signals
        return trend_exit_signals(panels, settings)
    if variant == 'kdj_filtered':
        from backtest.research_kdj_timing import signals as filtered_signals
        return filtered_signals(panels, settings)
    if variant not in ('kdj', 'bollinger', 'combo'):
        raise ValueError('未知反转规则：' + str(variant))
    from factors.research_indicators import calculate
    from backtest.research_rule_strategies import confirmed
    close = panels['close']
    n, smooth = int(settings['kdj_period']), int(settings['kdj_smooth'])
    k = calculate(dict(operation='kdj_k', params=dict(n=n, smooth=smooth)), panels)
    d = calculate(dict(operation='kdj_d', params=dict(n=n, smooth=smooth)), panels)
    mid = close.rolling(int(settings['bollinger_period'])).mean()
    sd = close.rolling(int(settings['bollinger_period'])).std(ddof=0)
    lower = mid - float(settings['bollinger_k']) * sd
    upper = mid + float(settings['bollinger_k']) * sd
    position = (close - lower) / (upper - lower).replace(0, np.nan)
    known = close.notna() & k.notna() & d.notna() & position.notna()
    lookback = int(settings['setup_lookback'])
    low_k = k.le(settings['oversold']) & d.le(settings['oversold'])
    high_k = k.ge(settings['overbought']) & d.ge(settings['overbought'])
    recent_low = low_k.astype(float).rolling(lookback, min_periods=1).max().eq(1)
    recent_high = high_k.astype(float).rolling(lookback, min_periods=1).max().eq(1)
    cross_up = k.gt(d) & k.shift().le(d.shift())
    cross_down = k.lt(d) & k.shift().ge(d.shift())
    low_band = position.le(settings['low_position']).astype(float).rolling(lookback, min_periods=1).max().eq(1)
    high_band = position.ge(settings['high_position']).astype(float).rolling(lookback, min_periods=1).max().eq(1)
    reclaim = position.gt(settings['low_position']) & position.shift().le(settings['low_position'])
    confirmation = int(settings['confirmation_lookback'])
    recent_cross = cross_up.astype(float).rolling(confirmation, min_periods=1).max().eq(1)
    recent_reclaim = reclaim.astype(float).rolling(confirmation, min_periods=1).max().eq(1)
    k_entry = recent_low & recent_cross & k.gt(d) & close.gt(close.shift())
    b_entry = low_band & recent_reclaim & position.gt(settings['low_position']) & close.gt(close.shift())
    if variant == 'kdj':
        entry, high_exit = k_entry, recent_high & cross_down
    elif variant == 'bollinger':
        entry = b_entry
        high_exit = high_band & position.lt(settings['high_position']) & position.shift().ge(settings['high_position'])
    else:
        entry = low_band & recent_low & k.gt(d) & position.gt(settings['low_position']) & (recent_cross | recent_reclaim) & close.gt(close.shift())
        high_exit = high_band & recent_high & cross_down
    trend = close.rolling(int(settings['trend_period'])).mean()
    below = confirmed(close.lt(trend), known & trend.notna(), int(settings['trend_confirm'])).eq(1)
    # Price must have been above its average recently, then close below it
    # twice while the average turns down. Actual positions belong exclusively
    # to the shared execution ledger, not to this signal calculator.
    recently_above = close.ge(trend).astype(float).rolling(int(settings['trend_period']), min_periods=1).max().eq(1)
    trend_exit = recently_above & below & trend.lt(trend.shift())
    exit_signal = high_exit | trend_exit
    return dict(_entry=(entry & ~exit_signal).astype(float).where(known), _exit=exit_signal.astype(float).where(known)), dict(
        k=k, d=d, mid=mid, lower=lower, upper=upper, position=position, trend=trend,
        signal_known=known, recent_low=recent_low, recent_high=recent_high,
        cross_up=cross_up, cross_down=cross_down,
        high_exit=high_exit, trend_exit=trend_exit)
