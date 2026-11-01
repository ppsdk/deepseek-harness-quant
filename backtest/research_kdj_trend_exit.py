"""Causal KDJ entry with independent DMI / long-average technical exits.

Dependencies: pandas/numpy and the existing OHLC indicator calculator. No
holding state or execution lives here. validate() declares required warmup;
all parameters come from the profile. ADX is diagnostic, never a hidden gate.
"""
import numpy as np


def validate(settings):
    for key in ('kdj_period', 'kdj_smooth', 'setup_lookback', 'confirmation_lookback',
                'dmi_period', 'dmi_confirm', 'exit_period', 'exit_confirm',
                'exit_slope_lookback', 'min_trade_interval_days'):
        value = settings.get(key)
        minimum = 2 if key.endswith('period') else 1
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f'{key} 必须是至少{minimum}的整数。')
    threshold = settings.get('oversold')
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not np.isfinite(threshold) or not 0 <= threshold <= 100:
        raise ValueError('oversold 必须在0至100之间。')
    if settings.get('exit_mode') not in ('dmi', 'ma', 'dmi_and_ma'):
        raise ValueError('exit_mode 仅支持 dmi / ma / dmi_and_ma。')
    if not isinstance(settings.get('exit_require_falling'), bool):
        raise ValueError('exit_require_falling 必须是布尔值。')
    # Reject old sell-related KDJ knobs rather than silently ignoring them.
    allowed = {'kdj_period', 'kdj_smooth', 'setup_lookback', 'confirmation_lookback',
               'dmi_period', 'dmi_confirm', 'exit_period', 'exit_confirm',
               'exit_slope_lookback', 'min_trade_interval_days', 'oversold',
               'exit_mode', 'exit_require_falling'}
    if set(settings) - allowed:
        raise ValueError('KDJ买入/趋势退出不支持参数：' + ','.join(sorted(set(settings)-allowed)))
    return max(settings['kdj_period'] + 2*(settings['kdj_smooth']-1),
               2*settings['dmi_period'] + settings['dmi_confirm'],
               settings['exit_period'] + settings['exit_slope_lookback'] + settings['exit_confirm'])


def signals(panels, settings):
    from factors.research_indicators import calculate
    from backtest.research_rule_strategies import confirmed
    validate(settings)
    close = panels['close'].astype(float)
    def indicator(op, n, **extra):
        return calculate(dict(operation=op, params=dict(n=n, **extra)), panels)
    k = indicator('kdj_k', settings['kdj_period'], smooth=settings['kdj_smooth'])
    d = indicator('kdj_d', settings['kdj_period'], smooth=settings['kdj_smooth'])
    entry_known = close.notna() & close.shift().notna() & k.notna() & d.notna()
    recent_low = (k.le(settings['oversold']) & d.le(settings['oversold'])).astype(float).where(entry_known).rolling(
        settings['setup_lookback'], min_periods=1).max().eq(1)
    cross_up = k.gt(d) & k.shift().le(d.shift())
    recent_cross = cross_up.astype(float).where(entry_known).rolling(settings['confirmation_lookback'], min_periods=1).max().eq(1)
    entry = recent_low & recent_cross & k.gt(d) & close.gt(close.shift())

    pdi = indicator('dmi_plus', settings['dmi_period'])
    mdi = indicator('dmi_minus', settings['dmi_period'])
    adx = indicator('adx', settings['dmi_period'])
    dmi_known = close.notna() & pdi.notna() & mdi.notna()
    dmi_weak = pdi.lt(mdi)
    dmi_exit = confirmed(dmi_weak, dmi_known, settings['dmi_confirm']).eq(1)
    ma = close.rolling(settings['exit_period'], min_periods=settings['exit_period']).mean()
    ma_known = close.notna() & ma.notna()
    ma_weak = close.lt(ma)
    ma_exit = confirmed(ma_weak, ma_known, settings['exit_confirm']).eq(1)
    if settings['exit_require_falling']:
        ma_known &= ma.shift(settings['exit_slope_lookback']).notna()
        ma_exit &= ma.lt(ma.shift(settings['exit_slope_lookback']))
    mode = settings['exit_mode']
    if mode == 'dmi':
        leave, exit_known, weakening = dmi_exit, dmi_known, dmi_weak
    elif mode == 'ma':
        leave, exit_known, weakening = ma_exit, ma_known, ma_weak
    else:
        leave, exit_known = dmi_exit & ma_exit, dmi_known & ma_known
        weakening = dmi_weak | ma_weak
    # Exits depend only on the selected trend inputs, even if KDJ is missing.
    return dict(_entry=(entry & ~leave).astype(float).where(entry_known & exit_known),
                _exit=leave.astype(float).where(exit_known)), dict(
        k=k, d=d, trend=ma, exit_ma=ma, pdi=pdi, mdi=mdi, adx=adx,
        known=entry_known & exit_known, exit_known=exit_known,
        recent_low=recent_low, cross_up=cross_up,
        dmi_exit=dmi_exit, trend_exit=ma_exit, exit_weakening=weakening,
        dmi_selected=close.notna() & (mode != 'ma'),
        ma_selected=close.notna() & (mode != 'dmi'))
