"""KDJ setup with bullish confirmation and two independent trend exits.

Reuses the causal KDJ/DMI adapter and the shared portfolio ledger. A setup is
valid for a configured trailing window; this module keeps no shadow holdings.
Research capability: causal OHLC timing, pending reversal confirmation,
strong-trend decay and weak-rebound failure. No parameter fitting or pivots.
"""
EXTRA_KEYS = {'entry_dmi_confirm', 'entry_ma_period', 'entry_slope_lookback',
              'weak_exit_period', 'weak_exit_slope_lookback', 'weak_exit_confirm'}


def base_settings(settings):
    return {key: value for key, value in settings.items() if key not in EXTRA_KEYS}


def validate(settings):
    from backtest.research_kdj_trend_exit import validate as validate_base
    warmup = validate_base(base_settings(settings))
    if settings.get('exit_mode') != 'dmi_weakening':
        raise ValueError('kdj_regime 必须使用显式 dmi_weakening 衰减退出。')
    for key in EXTRA_KEYS:
        value = settings.get(key)
        minimum = 2 if key.endswith('period') else 1
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f'{key} 必须是至少{minimum}的整数。')
    return max(warmup, settings['entry_ma_period'] + settings['entry_slope_lookback'],
               settings['weak_exit_period'] + settings['weak_exit_slope_lookback'] + settings['weak_exit_confirm'])


def signals(panels, settings):
    from backtest.research_kdj_trend_exit import signals as base_signals
    from backtest.research_rule_strategies import confirmed
    validate(settings)
    adapters, diag = base_signals(panels, base_settings(settings))
    close = panels['close'].astype(float)
    entry_ma = close.rolling(settings['entry_ma_period'], min_periods=settings['entry_ma_period']).mean()
    exit_ma = close.rolling(settings['weak_exit_period'], min_periods=settings['weak_exit_period']).mean()
    pdi, mdi = diag['pdi'], diag['mdi']
    dmi_known = pdi.notna() & mdi.notna()
    bullish = confirmed(pdi.gt(mdi), dmi_known, settings['entry_dmi_confirm']).eq(1)
    entry_known = diag['known'] & entry_ma.notna() & entry_ma.shift(settings['entry_slope_lookback']).notna()
    entry_gate = bullish & close.gt(entry_ma) & entry_ma.gt(entry_ma.shift(settings['entry_slope_lookback']))
    bearish = confirmed(pdi.lt(mdi), dmi_known, settings['dmi_confirm']).eq(1)
    weak_known = dmi_known & close.notna() & exit_ma.notna() & exit_ma.shift(settings['weak_exit_slope_lookback']).notna()
    weak_condition = bearish & close.lt(exit_ma) & exit_ma.lt(exit_ma.shift(settings['weak_exit_slope_lookback']))
    weak_exit = confirmed(weak_condition, weak_known, settings['weak_exit_confirm']).eq(1)
    exit_known = diag['exit_known'] & weak_known
    leave = adapters['_exit'].eq(1) | weak_exit
    entry = adapters['_entry'].eq(1) & entry_gate & ~leave
    # KDJ affects only entry. Both exit branches remain calculable when its
    # inputs are unknown; sell priority and fills remain with the shared ledger.
    diag.update(known=entry_known & exit_known, exit_known=exit_known,
                exit_ma=exit_ma, entry_ma=entry_ma, long_ma=diag['trend'],
                weak_rebound_exit=weak_exit, bullish_confirmation=bullish,
                entry_trend_confirmation=entry_gate,
                exit_weakening=diag['exit_weakening'] | weak_condition)
    return dict(_entry=entry.astype(float).where(entry_known & exit_known),
                _exit=leave.astype(float).where(exit_known)), diag
