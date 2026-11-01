"""Causal redundancy filtering shared by ordinary and registered factor blends."""
from copy import deepcopy
import math

import numpy as np
import pandas as pd


def settings(section, override=None):
    from factors.research_library import read_config
    result = deepcopy(read_config('research_diversification')[section])
    if override is not None:
        if not isinstance(override, dict) or set(override) - set(result):
            raise ValueError('未知分散配置字段：' + section)
        result.update(override)
    for key, value in result.items():
        if key in ('enabled',):
            if not isinstance(value, bool):
                raise ValueError(key + '必须为布尔值。')
        elif key.startswith('min_') or key in ('window', 'max_runs'):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 1 or int(value) != value:
                raise ValueError(key + '必须为正整数。')
            result[key] = int(value)
        elif key.startswith('max_') or key.endswith('_cap'):
            if value is None and not key.endswith('_cap'):
                raise ValueError(key + '不能为空。')
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 1):
                raise ValueError(key + '必须在 (0, 1] 内。')
        elif key in ('missing_policy', 'unknown_policy'):
            if value not in (('keep', 'block') if key == 'unknown_policy' else ('block', 'allow')):
                raise ValueError('未知缺失值策略。')
    if section == 'signal' and result['min_days'] > result['window']:
        raise ValueError('min_days 不能超过 window。')
    if section == 'portfolio' and result['min_observations'] > result['window']:
        raise ValueError('min_observations 不能超过 window。')
    return result


def combine(frames, weights, names, panels, *, minimum=None, policy=None):
    """Filter past signal ranks; today's values never fit today's selection.

    The caller supplies oriented rank frames. Eligibility predicates are owned
    by the caller and must be applied even when a redundant rank is removed.
    """
    cfg = settings('signal', policy if policy is not None else panels.get('_signal_policy'))
    if not frames or len(frames) != len(weights) or len(names) != len(frames):
        raise ValueError('复合因子的输入、名称和权重数量必须一致。')
    w = np.asarray(weights, dtype=float)
    if not np.isfinite(w).all() or (w <= 0).any():
        raise ValueError('因子权重必须为正数。')
    reference = frames[0]
    if any(not f.index.equals(reference.index) or not f.columns.equals(reference.columns) for f in frames):
        raise ValueError('因子面板必须完全对齐。')
    values = np.stack([f.to_numpy(dtype=float) for f in frames], axis=2)
    selected = np.ones((len(reference), len(frames)), dtype=bool)
    pairs = {}
    if cfg['enabled'] and len(frames) > 1:
        if not reference.index.is_monotonic_increasing or not reference.index.is_unique:
            raise ValueError('因子日期必须唯一并按时间递增。')
        def trailing(x):
            return pd.Series(x).shift(1).rolling(cfg['window'], min_periods=1).sum().to_numpy()
        for i in range(len(frames)):
            for j in range(i):
                a, b = values[:, :, i], values[:, :, j]
                valid = np.isfinite(a) & np.isfinite(b)
                x, y = np.where(valid, a, 0), np.where(valid, b, 0)
                n = trailing(valid.sum(axis=1))
                sx, sy = trailing(x.sum(axis=1)), trailing(y.sum(axis=1))
                variance = (n * trailing((x*x).sum(axis=1)) - sx*sx) * (n * trailing((y*y).sum(axis=1)) - sy*sy)
                numerator = n * trailing((x*y).sum(axis=1)) - sx*sy
                with np.errstate(invalid='ignore', divide='ignore'):
                    corr = numerator / np.sqrt(np.maximum(variance, 0))
                known = (n >= cfg['min_samples']) & (trailing(valid.any(axis=1).astype(int)) >= cfg['min_days']) & (variance > 1e-14)
                pairs[(j, i)] = np.where(known, np.clip(corr, -1, 1), np.nan)
        order = sorted(range(len(frames)), key=lambda i: (-w[i], i))
        for t in range(len(reference)):
            kept = []
            selected[t] = False
            for i in order:
                correlations = [pairs[tuple(sorted((i, j)))][t] for j in kept]
                if any(np.isfinite(c) and abs(c) > cfg['max_abs_correlation'] for c in correlations):
                    continue
                if cfg['unknown_policy'] == 'block' and any(not np.isfinite(c) for c in correlations):
                    continue
                selected[t, i] = True
                kept.append(i)
    valid = np.isfinite(values) & selected[:, None, :]
    denominator = (valid * w).sum(axis=2)
    result = np.where(valid, values * w, 0).sum(axis=2) / np.where(denominator > 0, denominator, np.nan)
    required = selected.sum(axis=1)[:, None] if minimum is None else np.minimum(minimum, selected.sum(axis=1))[:, None]
    result[(valid.sum(axis=2) < required) | (required == 0)] = np.nan
    # Compress dates with unchanged selections, retain causal statistics at changes.
    changes = []
    for t in range(len(reference)):
        if t == 0 or not np.array_equal(selected[t], selected[t-1]):
            changes.append(dict(date=str(reference.index[t]), selected=[names[i] for i in np.flatnonzero(selected[t])],
                                dropped=[names[i] for i in np.flatnonzero(~selected[t])]))
    panels.setdefault('_signal_correlation_audit', []).append(dict(
        factors=list(names), settings=cfg, fit_boundary='strictly_before_score_date',
        priority='descending_weight_then_input_order', changes=changes,
        latest_pairs=[dict(left=names[i], right=names[j], correlation=float(v[-1]) if len(v) and np.isfinite(v[-1]) else None)
                      for (i, j), v in pairs.items()],
        insufficient_history_policy=cfg['unknown_policy']))
    return pd.DataFrame(result, index=reference.index, columns=reference.columns)
