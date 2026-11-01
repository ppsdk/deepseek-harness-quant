"""Causal KDJ adapter; no holdings, fills, or account state live here.

Requires continuous point-in-time OHLC and volume. Outputs _entry/_exit for
the shared ledger; unknown inputs cannot create a buy or technical sell.
"""
import numpy as np


def validate(settings):
    integer_keys = ('kdj_period', 'kdj_smooth', 'setup_lookback',
                    'confirmation_lookback', 'trend_fast_period',
                    'trend_slow_period', 'trend_slope_lookback',
                    'exit_period', 'exit_confirm', 'volume_period',
                    'volume_confirm_lookback')
    for key in integer_keys:
        value = settings[key]
        minimum = 2 if key.endswith('period') else 1
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)) or value < minimum:
            raise ValueError(f'{key} 必须是至少 {minimum} 的整数。')
    for key in ('oversold', 'overbought', 'trend_max_below_slow', 'volume_min_ratio'):
        value = settings[key]
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, (float, int)) or not np.isfinite(value):
            raise ValueError(f'{key} 必须是有限数值。')
    if not 0 <= settings['oversold'] < settings['overbought'] <= 100:
        raise ValueError('KDJ 阈值必须满足 0 <= oversold < overbought <= 100。')
    if not 0 <= settings['trend_max_below_slow'] < 1 or settings['volume_min_ratio'] <= 0:
        raise ValueError('趋势容差或量比参数无效。')
    if settings['trend_fast_period'] >= settings['trend_slow_period']:
        raise ValueError('趋势短均线周期必须小于长均线周期。')
    for key in ('trend_enabled', 'volume_enabled'):
        if not isinstance(settings[key], bool):
            raise ValueError(f'{key} 必须是布尔值。')
    return max(settings['kdj_period'] + 2 * (settings['kdj_smooth'] - 1),
               settings['trend_slow_period'] + settings['trend_slope_lookback'],
               settings['exit_period'] + settings['exit_confirm'], settings['volume_period'])


def signals(panels, settings):
    from factors.research_indicators import calculate
    from backtest.research_rule_strategies import confirmed
    validate(settings)
    close = panels['close'].astype(float)
    n = int(settings['kdj_period']); smooth = int(settings['kdj_smooth'])
    k = calculate(dict(operation='kdj_k', params=dict(n=n, smooth=smooth)), panels)
    d = calculate(dict(operation='kdj_d', params=dict(n=n, smooth=smooth)), panels)
    known = close.notna() & k.notna() & d.notna()
    lookback = int(settings.get('setup_lookback', 5))
    low = k.le(float(settings['oversold'])) & d.le(float(settings['oversold']))
    high = k.ge(float(settings['overbought'])) & d.ge(float(settings['overbought']))
    recent_low = low.astype(float).rolling(lookback, min_periods=1).max().eq(1)
    recent_high = high.astype(float).rolling(lookback, min_periods=1).max().eq(1)
    cross_up = k.gt(d) & k.shift().le(d.shift())
    cross_down = k.lt(d) & k.shift().ge(d.shift())
    confirm = int(settings.get('confirmation_lookback', 3))
    recent_cross = cross_up.astype(float).rolling(confirm, min_periods=1).max().eq(1)
    entry = recent_low & recent_cross & k.gt(d) & close.gt(close.shift())
    high_exit = recent_high & cross_down
    exit_signal = high_exit.copy()

    fast_n = int(settings.get('trend_fast_period', 20))
    slow_n = int(settings.get('trend_slow_period', 60))
    slope_n = int(settings.get('trend_slope_lookback', 5))
    fast = close.rolling(fast_n, min_periods=fast_n).mean()
    slow = close.rolling(slow_n, min_periods=slow_n).mean()
    slope = slow - slow.shift(slope_n)
    max_below = float(settings.get('trend_max_below_slow', .02))
    volume = panels.get('volume')
    if volume is None or not volume.index.equals(close.index) or not volume.columns.equals(close.columns):
        raise ValueError('kdj_filtered 需要 volume 面板。')
    volume_n = int(settings.get('volume_period', 20))
    # Compare completed-day volume to the preceding sessions, excluding the
    # current bar from its own baseline. Invalid volume is never filled.
    volume = volume.astype(float).where(lambda x: np.isfinite(x) & x.gt(0))
    volume_ratio = volume / volume.shift().rolling(volume_n, min_periods=volume_n).mean().replace(0, np.nan)
    volume_threshold = float(settings.get('volume_min_ratio', .8))
    volume_confirm = int(settings.get('volume_confirm_lookback', 3))
    recent_volume = volume_ratio.ge(volume_threshold).astype(float).rolling(volume_confirm, min_periods=1).max().eq(1)
    trend_filter = fast.gt(slow) & slope.gt(0) & close.ge(slow * (1.0 - max_below))
    if settings['trend_enabled']:
        entry &= trend_filter
        known &= fast.notna() & slope.notna()
    if settings['volume_enabled']:
        entry &= recent_volume
        known &= volume_ratio.notna()
    exit_ma = close.rolling(settings['exit_period']).mean()
    exit_known = close.notna() & exit_ma.notna()
    trend_exit = confirmed(close.lt(exit_ma), exit_known, settings['exit_confirm']).eq(1) & exit_ma.lt(exit_ma.shift())
    exit_signal |= trend_exit
    known &= exit_ma.notna()
    return dict(_entry=(entry & ~exit_signal).astype(float).where(known),
                _exit=exit_signal.astype(float).where(known)), dict(
        k=k, d=d, trend=slow, fast=fast, volume_ratio=volume_ratio,
        trend_filter=trend_filter, exit_ma=exit_ma, known=known,
        recent_low=recent_low, recent_high=recent_high,
        cross_up=cross_up, cross_down=cross_down,
        high_exit=high_exit, trend_exit=trend_exit)
