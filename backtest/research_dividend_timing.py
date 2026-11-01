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
