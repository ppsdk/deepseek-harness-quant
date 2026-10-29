"""Shared, extensible evaluation field definitions for research APIs.

The UI consumes these schemas instead of knowing which factor family or
strategy produced a row.  New mining jobs can add a value to ``evaluation``
without requiring another page or another table implementation.
"""

from __future__ import annotations


FACTOR_SCHEMA = [
    {"id": "rank_ic", "label": "RankIC", "format": "number", "empty": "未检验"},
    {"id": "ic_ir", "label": "ICIR", "format": "number", "empty": "未检验"},
    {"id": "ic_positive_rate", "label": "IC 正向率", "format": "percent", "empty": "未检验"},
    {"id": "coverage", "label": "覆盖率", "format": "percent", "empty": "未检验"},
    {"id": "evaluated_days", "label": "有效检验日", "format": "integer", "empty": "未检验"},
    {"id": "long_short_spread", "label": "多空分组差", "format": "percent", "empty": "未检验"},
    {"id": "sample_stocks_mean", "label": "平均样本数", "format": "number", "empty": "未检验"},
    {"id": "hac_t", "label": "HAC t 值", "format": "number", "empty": "未检验"},
    {"id": "p_value", "label": "p 值", "format": "scientific", "empty": "未检验"},
    {"id": "direction_stability", "label": "方向稳定率", "format": "percent", "empty": "未检验"},
]

PARAMETER_SCHEMA = [
    {"id": "annual_return", "label": "验证年化", "format": "percent", "empty": "未检验"},
    {"id": "default_annual_return", "label": "默认年化", "format": "percent", "empty": "未检验"},
    {"id": "return_delta", "label": "相对默认变化", "format": "percent", "empty": "未检验"},
    {"id": "max_drawdown", "label": "最大回撤", "format": "percent", "empty": "未检验"},
    {"id": "annual_one_way_turnover", "label": "单边年换手", "format": "number", "empty": "未检验"},
    {"id": "pressure_annual_return", "label": "压力年化", "format": "percent", "empty": "未检验"},
    {"id": "pressure_max_drawdown", "label": "压力回撤", "format": "percent", "empty": "未检验"},
    {"id": "average_exposure", "label": "平均仓位", "format": "percent", "empty": "未检验"},
]

STRATEGY_SCHEMA = [
    {"id": "annual_return", "label": "年化收益", "format": "percent", "empty": "未回测"},
    {"id": "max_drawdown", "label": "最大回撤", "format": "percent", "empty": "未回测"},
    {"id": "sharpe", "label": "夏普", "format": "number", "empty": "未回测"},
    {"id": "annual_one_way_turnover", "label": "单边年换手", "format": "number", "empty": "未回测"},
    {"id": "verdict", "label": "结论", "format": "text", "empty": "未回测"},
    {"id": "evaluated_runs", "label": "回测次数", "format": "integer", "empty": "未回测"},
]


def _state(*, available: bool, complete: bool) -> str:
    if complete and available:
        return "ready"
    if available:
        return "partial"
    return "insufficient"


def axis_evaluation(record: dict, horizon: int = 20) -> dict:
    """Normalize a four-axis record to the common factor evaluation shape."""
    h = (record.get("horizons") or {}).get(str(horizon)) or {}
    observations = h.get("observations")
    spread = h.get("signed_top_bottom_spread", record.get("spread"))
    yearly = h.get("yearly_rank_ic") or {}
    positive_years = [float(v) > 0 for v in yearly.values() if v is not None]
    return {
        "state": _state(available=observations is not None, complete=bool(record.get("effective"))),
        "rank_ic": h.get("rank_ic_mean", record.get("rank_ic")),
        "ic_ir": h.get("rank_ic_ir"),
        # Historical mining ledgers predate the daily-rate field.  Use the
        # auditable year-level sign ratio as the compatible fallback.
        "ic_positive_rate": h.get("ic_positive_rate", (sum(positive_years) / len(positive_years) if positive_years else None)),
        "coverage": record.get("coverage"),
        "evaluated_days": observations or 0,
        "long_short_spread": spread,
        "sample_stocks_mean": record.get("universe_stocks"),
        "hac_t": h.get("hac_t", record.get("hac_t")),
        "p_value": h.get("p_value"),
        "direction_stability": h.get("year_direction_fraction", record.get("year_stability")),
    }


def parameter_evaluation(row: dict) -> dict:
    """Normalize parameter/portfolio research while preserving raw fields."""
    annual = row.get("annual_return")
    default = row.get("default_annual_return")
    return {
        "state": "ready" if annual is not None else "insufficient",
        "annual_return": annual,
        "default_annual_return": default,
        "return_delta": annual - default if annual is not None and default is not None else None,
        "max_drawdown": row.get("max_drawdown"),
        "annual_one_way_turnover": row.get("turnover", row.get("annual_one_way_turnover")),
        "pressure_annual_return": row.get("pressure_annual_return"),
        "pressure_max_drawdown": row.get("pressure_max_drawdown"),
        "average_exposure": row.get("average_exposure"),
    }


def strategy_evaluation(row: dict | None = None) -> dict:
    """Stable summary for a strategy directory entry.

    A strategy can exist before any run is archived; callers therefore always
    receive the same shape and can render a clear ``pending`` state.
    """
    row = row or {}
    metrics = row.get("metrics") or {}
    runs = int(row.get("evaluated_runs") or 0)
    annual = metrics.get("annual_return", row.get("annual_return"))
    return {
        "state": "ready" if runs and annual is not None else "pending",
        "annual_return": annual,
        "max_drawdown": metrics.get("max_drawdown", row.get("max_drawdown")),
        "sharpe": metrics.get("sharpe", row.get("sharpe")),
        "annual_one_way_turnover": row.get("annual_one_way_turnover", row.get("turnover", metrics.get("annual_one_way_turnover"))),
        "verdict": row.get("verdict"),
        "evaluated_runs": runs,
    }


__all__ = ["FACTOR_SCHEMA", "PARAMETER_SCHEMA", "STRATEGY_SCHEMA", "axis_evaluation", "parameter_evaluation", "strategy_evaluation"]
