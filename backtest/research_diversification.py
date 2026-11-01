"""Shared portfolio constraints at target sizing and actual buy execution."""
import pandas as pd
import numpy as np

from factors.signal_blend import settings


def policy(panels, params=None):
    override = (params or {}).get('diversification', {}).get('portfolio', panels.get('_portfolio_policy'))
    return settings('portfolio', override)


def pair_correlation(panels, left, right, day, cfg):
    # Independent of the day's executable open: risk uses prior-close history.
    key = (str(day), left, right, cfg['window'], cfg['min_observations'])
    cache = panels.setdefault('_risk_pair_cache', {})
    if key not in cache:
        hist = panels['close'].loc[:day, [left, right]].tail(cfg['window']+1).pct_change(fill_method=None).dropna()
        value = hist.iloc[:, 0].corr(hist.iloc[:, 1]) if len(hist) >= cfg['min_observations'] else np.nan
        cache[key] = float(value) if np.isfinite(value) else None
    return cache[key]


def buy_room(panels, code, current_weights, day, cfg):
    """Return maximum additional NAV weight; never enlarge or fabricate data."""
    if not cfg['enabled']:
        return 1., None
    for previous, weight in current_weights.items():
        if previous == code or weight <= 1e-12:
            continue
        corr = pair_correlation(panels, code, previous, day, cfg)
        if corr is None and cfg['missing_policy'] == 'block':
            return 0., 'correlation_history_missing'
        if corr is not None and abs(corr) > cfg['max_abs_correlation']:
            return 0., 'portfolio_correlation_limit'
    from data.research_exposures import attach
    room, reason = 1., None
    for dim, panel in attach(panels).items():
        cap = cfg['industry_cap'] if dim == 'industry' else cfg['style_cap']
        if cap is None:
            continue
        value = panel.at[day, code]
        if pd.isna(value):
            if cfg['missing_policy'] == 'block':
                return 0., 'exposure_history_missing:' + dim
            continue
        used = 0.
        for held, weight in current_weights.items():
            label = panel.at[day, held]
            if pd.isna(label):
                if cfg['missing_policy'] == 'block':
                    return 0., 'held_exposure_history_missing:' + dim
            elif label == value:
                used += weight
        available = max(0., cap-used)
        if available < room:
            room, reason = available, 'concentration_limit:' + dim
    return room, reason


def constrain_targets(panels, weights, day, params):
    cfg = policy(panels, params)
    if not cfg['enabled']:
        return weights
    selected = {}
    for code, weight in weights.items():
        room, reason = buy_room(panels, code, selected, day, cfg)
        actual = min(weight, room)
        if actual > 1e-12:
            selected[code] = actual
        if actual + 1e-12 < weight:
            panels.setdefault('_portfolio_constraint_audit', []).append(dict(
                date=str(day), code=code, requested_weight=float(weight), target_weight=float(actual), reason=reason))
    return selected


def snapshot(panels, weights, day, cfg):
    from data.research_exposures import attach, concentration
    labels = attach(panels)
    exposures = concentration(weights, labels, day)
    correlations, missing = [], 0
    codes = [c for c, w in weights.items() if w > 1e-12]
    for i, left in enumerate(codes):
        for right in codes[:i]:
            value = pair_correlation(panels, left, right, day, cfg)
            if value is None:
                missing += 1
            else:
                correlations.append(abs(value))
    violations = []
    for dim, report in exposures.items():
        cap = cfg['industry_cap'] if dim == 'industry' else cfg['style_cap']
        if cap is not None and report['max_weight'] > cap+1e-8:
            violations.append('concentration_limit:' + dim)
        if cap is not None and report['unknown_weight'] > 1e-12:
            violations.append('unverified_exposure:' + dim)
    if correlations and max(correlations) > cfg['max_abs_correlation']:
        violations.append('portfolio_correlation_limit')
    return dict(date=str(day), exposures=exposures, max_abs_correlation=max(correlations, default=None),
                missing_correlation_pairs=missing, violations=violations,
                constraints_enabled=cfg['enabled'])
