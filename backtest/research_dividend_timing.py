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
