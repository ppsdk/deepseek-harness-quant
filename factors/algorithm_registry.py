"""统一的复杂因子算法注册与输出边界。

算法通过配置声明 ``operation``，由本模块负责参数校验、分发和结果对齐。
这样新增研究算法只需注册后端处理器，不需要新增前端页面或修改策略流程。
"""
from dataclasses import dataclass
import math
from typing import Callable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Algorithm:
    name: str
    handler: Callable
    metadata: dict


_REGISTRY: dict[str, Algorithm] = {}


def register_factor_algorithm(name, handler, **metadata):
    if not isinstance(name, str) or not name.strip() or not callable(handler):
        raise ValueError("算法名称和处理器不能为空。")
    _REGISTRY[name] = Algorithm(name, handler, {"algorithm_id": name, **metadata})
    return handler


def algorithm_catalog():
    return {name: dict(item.metadata) for name, item in _REGISTRY.items()}


def validate_algorithm_spec(spec):
    operation = str(spec.get("operation", "")).strip()
    if operation not in _REGISTRY:
        raise ValueError("未注册的复杂算法：" + operation)
    params = spec.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError("复杂算法 params 必须是对象。")
    return _REGISTRY[operation], params


def validate_factor_output(raw, panels, name="factor"):
    close = panels.get("close")
    if not isinstance(raw, pd.DataFrame) or not isinstance(close, pd.DataFrame):
        raise ValueError(f"{name} 算法输出必须是 DataFrame。")
    if not raw.index.equals(close.index) or not raw.columns.equals(close.columns):
        raise ValueError(f"{name} 算法输出必须与 close 的日期和股票列完全对齐。")
    raw = raw.astype(float).replace([np.inf, -np.inf], np.nan)
    return raw.where(close.notna())


def run_registered_algorithm(spec, panels, definitions=None):
    algorithm, params = validate_algorithm_spec(spec)
    result = algorithm.handler(spec, panels, definitions or {})
    return validate_factor_output(result, panels, spec.get("id") or algorithm.name)


def _rolling_pca_score(spec, panels, definitions):
    """历史滚动窗口 PCA 复合因子，只使用当前日期之前的数据拟合。"""
    from sklearn.decomposition import PCA
    from factors.research_library import raw_factor

    params = spec.get("params") or {}
    window = int(params.get("window", 120))
    components = int(params.get("components", 1))
    min_samples = int(params.get("min_samples", max(30, components * 10)))
    if window < 2 or components < 1 or min_samples < components:
        raise ValueError("rolling_pca_score 的 window、components、min_samples 参数无效。")
    names = list(spec.get("dependencies") or params.get("features") or [])
    if len(names) < components:
        raise ValueError("rolling_pca_score 至少需要与主成分数相等的依赖因子。")
    features = [raw_factor(name, panels, definitions) for name in names]
    index, columns = panels["close"].index, panels["close"].columns
    values = [frame.reindex(index=index, columns=columns).to_numpy(float) for frame in features]
    out = np.full((len(index), len(columns)), np.nan, dtype=float)
    for t in range(len(index)):
        start = max(0, t - window)
        if t <= start:
            continue
        train = np.stack([v[start:t] for v in values], axis=2).reshape(-1, len(values))
        finite = np.isfinite(train).all(axis=1)
        train = train[finite]
        current = np.stack([v[t] for v in values], axis=1)
        valid = np.isfinite(current).all(axis=1)
        if len(train) < min_samples or valid.sum() == 0:
            continue
        mean, scale = train.mean(axis=0), train.std(axis=0)
        scale[scale == 0] = 1.0
        train_z = (train - mean) / scale
        current_z = (current - mean) / scale
        n_comp = min(components, train_z.shape[1], train_z.shape[0])
        model = PCA(n_components=n_comp, svd_solver="full").fit(train_z)
        # Component direction is otherwise arbitrary; make it stable for users.
        if model.components_[0, 0] < 0:
            model.components_[0] *= -1
        out[t, valid] = model.transform(current_z[valid])[:, 0]
    return pd.DataFrame(out, index=index, columns=columns)


def _weighted_rank_composite(spec, panels, definitions):
    """稳健加权截面复合因子，不依赖 PCA 或训练模型。

    每个交易日独立排名，按权重聚合有效因子；缺失值只从当日分母中移除，
    不以前后日期填补，适合把技术、基本面和机器学习输出混合进同一策略。
    """
    from factors.research_library import raw_factor

    names = list(spec.get("dependencies") or (spec.get("params") or {}).get("features") or [])
    params = spec.get("params") or {}
    weights = params.get("weights") or [1.0] * len(names)
    signs = params.get("signs") or [1.0] * len(names)
    min_features = int(params.get("min_features", 1))
    if not names or len(weights) != len(names) or len(signs) != len(names):
        raise ValueError("weighted_rank_composite 的 features、weights、signs 长度必须一致。")
    if min_features < 1 or min_features > len(names):
        raise ValueError("weighted_rank_composite 的 min_features 超出因子数量。")
    weights = np.asarray(weights, dtype=float)
    signs = np.asarray(signs, dtype=float)
    if (not np.isfinite(weights).all() or (weights <= 0).any()
            or not np.isin(signs, (-1, 1)).all()):
        raise ValueError("weighted_rank_composite 的权重必须为正数，方向必须为 1 或 -1。")
    frames = [raw_factor(name, panels, definitions).rank(axis=1, pct=True) * sign
              for name, sign in zip(names, signs)]
    stack = np.stack([frame.to_numpy(float) for frame in frames], axis=2)
    valid = np.isfinite(stack)
    count = valid.sum(axis=2)
    weighted = np.where(valid, stack * weights.reshape(1, 1, -1), 0.0)
    denominator = np.where(valid, weights.reshape(1, 1, -1), 0.0).sum(axis=2)
    result = weighted.sum(axis=2) / np.where(denominator == 0, np.nan, denominator)
    result[count < min_features] = np.nan
    return pd.DataFrame(result, index=panels["close"].index, columns=panels["close"].columns)


register_factor_algorithm(
    "rolling_pca_score", _rolling_pca_score,
    label="滚动PCA复合因子", category="复杂复合算法", kind="composite",
    research_capabilities=["factor", "screen", "backtest"],
    causal=True, output="cross_sectional_score",
)
register_factor_algorithm(
    "weighted_rank_composite", _weighted_rank_composite,
    label="稳健加权排名复合因子", category="复杂复合算法", kind="composite",
    research_capabilities=["factor", "screen", "backtest"],
    causal=True, output="cross_sectional_score",
)
