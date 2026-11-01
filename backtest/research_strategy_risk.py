"""Archived multi-strategy risk comparison and feasible NAV-budget allocation."""
from datetime import date
import json
import math
import re

import numpy as np
import pandas as pd

from factors.signal_blend import settings


def _returns(record, cutoff):
    rows = record.get('returns') or []
    series = pd.Series({r['date']: r['ret'] for r in rows}, dtype=float).sort_index()
    return series.loc[:cutoff].replace([np.inf, -np.inf], np.nan).dropna()


def _overlap(a, b, key, cutoff):
    left = {r['date']: r[key] for r in a if key in r and r['date'] <= cutoff}
    right = {r['date']: r[key] for r in b if key in r and r['date'] <= cutoff}
    days = sorted(set(left) & set(right))
    values, active, weighted = [], [], []
    for day in days:
        x, y = set(left[day]), set(right[day])
        union = x | y
        value = len(x & y)/len(union) if union else 0.
        values.append(value)
        if x or y:
            active.append(value)
        if key == 'position_weights':
            weighted.append(sum(min(left[day].get(c, 0.), right[day].get(c, 0.)) for c in union))
    return dict(state='ready' if days else 'unavailable', days=len(days), active_days=len(active),
                mean_jaccard=float(np.mean(values)) if values else None,
                active_mean_jaccard=float(np.mean(active)) if active else None,
                max_jaccard=max(values, default=None),
                mean_shared_nav_weight=float(np.mean(weighted)) if weighted else None)


def compare_records(records, cutoff, cfg=None, requested=None):
    """No fitted return forecasts: all statistics truncate at the supplied date."""
    cfg = settings('comparison', cfg)
    if not isinstance(cutoff, str) or date.fromisoformat(cutoff).isoformat() != cutoff:
        raise ValueError('as_of 必须为 YYYY-MM-DD。')
    names = list(records)
    if not 2 <= len(names) <= cfg['max_runs']:
        raise ValueError('策略比较数量超出配置范围。')
    returns = {key: _returns(r, cutoff) for key, r in records.items()}
    matrix = pd.DataFrame(returns)
    corr = matrix.corr(min_periods=cfg['min_observations'])
    pairs, lookup = [], {}
    for i, left in enumerate(names):
        for right in names[:i]:
            a, b = records[left], records[right]
            count = int(matrix[[left, right]].dropna().shape[0])
            c = corr.at[left, right]
            item = dict(left=left, right=right, return_days=count,
                        return_correlation=float(c) if np.isfinite(c) else None,
                        signal=_overlap(a.get('signal_candidates', []), b.get('signal_candidates', []), 'codes', cutoff),
                        holdings=_overlap(a.get('daily_account', []), b.get('daily_account', []), 'position_weights', cutoff))
            pairs.append(item)
            lookup[frozenset((left, right))] = item
    if requested is None:
        requested = {n: 1/len(names) for n in names}
    if not isinstance(requested, dict) or set(requested) != set(names):
        raise ValueError('weights 必须包含每个策略且不能含未知策略。')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in requested.values()) or sum(requested.values()) > 1+1e-10:
        raise ValueError('策略权重必须为非负 NAV 比例且总和不超过 1。')
    # Exposure budgets must share one observed account date; never carry forward
    # an old holding book or use the final book to describe an earlier date.
    accounts = {n: {r['date']: r for r in records[n].get('daily_account', []) if r['date'] <= cutoff}
                for n in names}
    common = set.intersection(*(set(v) for v in accounts.values()))
    allocation_day = max(common) if common else None
    selected, rejected, combined = {}, [], {}
    for n in sorted(names, key=lambda n: (-requested[n], names.index(n))):
        target = min(requested[n], cfg['max_strategy_weight'])
        reasons = []
        if target <= 0:
            continue
        if len(returns[n]) < cfg['min_observations']:
            reasons.append('own_return_history_missing')
        if target < requested[n]:
            rejected.append(dict(strategy=n, reason='max_strategy_weight', requested=requested[n], allowed=target))
        for held in selected:
            pair = lookup[frozenset((n, held))]
            c = pair['return_correlation']
            if c is None:
                if cfg['missing_policy'] == 'block':
                    reasons.append('return_history_missing:' + held)
            elif abs(c) > cfg['max_abs_return_correlation']:
                reasons.append('strategy_return_correlation:' + held)
            for field, threshold in [('signal', 'max_signal_overlap'), ('holdings', 'max_holding_overlap')]:
                overlap = pair[field]
                if overlap['days'] < cfg['min_observations']:
                    if cfg['missing_policy'] == 'block':
                        reasons.append(field + '_history_missing:' + held)
                elif (overlap['active_mean_jaccard'] or 0.) > cfg[threshold]:
                    reasons.append(field + '_overlap:' + held)
        exposures = accounts[n].get(allocation_day, {}).get('exposures', {})
        caps = {'industry': cfg['industry_cap'], 'style': cfg['style_cap']}
        if cfg['missing_policy'] == 'block':
            if any(v is not None for v in caps.values()) and len(matrix) and allocation_day != max(matrix.index):
                reasons.append('exposure_snapshot_stale_or_missing')
            if caps['industry'] is not None and 'industry' not in exposures:
                reasons.append('industry_exposure_missing')
            if caps['style'] is not None and not any(d.startswith('style:') for d in exposures):
                reasons.append('style_exposure_missing')
        for dim, report in exposures.items():
            cap = caps['industry'] if dim == 'industry' else caps['style']
            if cap is None:
                continue
            if report.get('unknown_weight', 0.) > 1e-12 and cfg['missing_policy'] == 'block':
                reasons.append('exposure_history_missing:' + dim)
            for group, mass in report.get('weights', {}).items():
                if mass > 0:
                    target = min(target, max(0., cap-combined.get(dim, {}).get(group, 0.))/mass)
        if reasons or target <= 1e-12:
            rejected.append(dict(strategy=n, reasons=reasons or ['combined_concentration_limit'], requested=requested[n], allowed=0.))
            continue
        selected[n] = target
        for dim, report in exposures.items():
            groups = combined.setdefault(dim, {})
            for group, mass in report.get('weights', {}).items():
                groups[group] = groups.get(group, 0.) + mass*target
        if target + 1e-12 < min(requested[n], cfg['max_strategy_weight']):
            rejected.append(dict(strategy=n, reason='combined_concentration_limit', requested=requested[n], allowed=target))
    # Aligned historical return series is a constant-weight, cost-excluded
    # diagnostic. Do not masquerade it as a new executable strategy backtest.
    complete = matrix[list(selected)].dropna() if selected else pd.DataFrame()
    combined_returns = complete.mul(pd.Series(selected)).sum(axis=1) if len(complete) else pd.Series(dtype=float)
    covariance = complete.cov() if len(complete) >= cfg['min_observations'] else pd.DataFrame()
    portfolio_vol, diversification_ratio = None, None
    if selected and len(covariance) and np.isfinite(covariance.to_numpy()).all():
        w = np.array([selected[n] for n in complete.columns])
        variance = float(w @ covariance.to_numpy() @ w)
        portfolio_vol = math.sqrt(max(0., variance))
        if portfolio_vol > 0:
            diversification_ratio = float(w @ np.sqrt(np.maximum(np.diag(covariance), 0)) / portfolio_vol)
    concentration = {}
    missing_books = [n for n in selected if not accounts[n].get(allocation_day, {}).get('exposures')]
    for dim, groups in combined.items():
        known = sum(groups.values())
        unknown = sum(selected[n] * accounts[n].get(allocation_day, {}).get('exposures', {}).get(dim, {}).get('unknown_weight', 0.) for n in selected)
        missing_dimension = [n for n in selected if dim not in accounts[n].get(allocation_day, {}).get('exposures', {})]
        concentration[dim] = dict(weights=groups, known_weight=known, unknown_weight=unknown,
                                  max_weight=max(groups.values(), default=0.),
                                  hhi=sum((v/known)**2 for v in groups.values()) if known else None,
                                  missing_strategies=missing_dimension,
                                  state='insufficient' if unknown > 1e-12 or missing_dimension else 'ready')
    return dict(ok=True, as_of=cutoff, statistics_end=max(matrix.index) if len(matrix) else None,
                pairwise=pairs, settings=cfg, requested_weights=requested,
                allocation_date=allocation_day, constrained_weights=selected,
                cash_weight=max(0., 1-sum(selected.values())), rejections=rejected,
                combined_exposure_weights=combined,
                combined_concentration=concentration, missing_exposure_books=missing_books,
                strategy_weight_hhi=sum(v*v for v in selected.values()),
                daily_combined_volatility=portfolio_vol, diversification_ratio=diversification_ratio,
                allocation_priority='descending_requested_weight_then_input_order',
                diagnostic_returns=[dict(date=str(d), ret=float(v)) for d, v in combined_returns.items()],
                allocation_status='feasible_on_observed_history' if selected else 'insufficient_or_conflicting',
                notes=['历史诊断不证明策略独立；共同择时和模型暴露仍可能重合。',
                       '组合收益为对齐日期的固定权重统计，不含组合再平衡成本，不是统一账本回测。',
                       '行业及风格权重按 NAV 统计；缺历史标签、旧归档无权重或无信号记录均不补造。'])


def compare(payload):
    from backtest.research_runner import ARCHIVES
    if not isinstance(payload, dict) or set(payload) - {'archive_ids', 'as_of', 'constraints', 'weights'}:
        raise ValueError('未知策略风险比较参数。')
    ids = payload.get('archive_ids')
    cfg = settings('comparison', payload.get('constraints'))
    if not isinstance(ids, list) or not 2 <= len(ids) <= cfg['max_runs'] or any(not isinstance(n, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,180}', n) for n in ids) or len(set(ids)) != len(ids):
        raise ValueError('archive_ids 必须为不重复的安全归档名称列表。')
    records = {}
    for name in ids:
        path = ARCHIVES/(name+'.json')
        if not path.is_file():
            raise ValueError('策略归档不存在：' + name)
        records[name] = json.loads(path.read_text(encoding='utf-8'))
    return compare_records(records, payload.get('as_of') or date.today().isoformat(), cfg, payload.get('weights'))
