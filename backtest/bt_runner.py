# -*- coding: utf-8 -*-
"""回测兼容入口。

研究页面的唯一回测实现是 :mod:`backtest.research_runner`。历史版本在本文件
复制了一套策略注册表、面板加载器和简化回测器，导致配置、成本口径和页面看到
的结果可能不一致。这里保留公开函数名，统一转发到研究引擎，旧调用方无需改
路径，但不会再产生第二套业务逻辑。
"""
from __future__ import annotations

from typing import Any


def list_strategies() -> dict:
    """返回当前配置和用户策略合并后的唯一策略目录。"""
    from backtest.research_runner import list_strategies as catalog

    return catalog()


def run_backtest(
    strategy: str = "tech3",
    topn: int | None = None,
    stocks: int | None = None,
    start: str | None = None,
    end: str | None = None,
    **kwargs: Any,
) -> dict:
    """执行标准研究回测。

    参数名兼容早期脚本；实际校验、数据加载、T+1 成交、成本模型和存档均由
    ``research_runner.run_research`` 负责。
    """
    from backtest.research_runner import run_research

    values = {"strategy": strategy, **kwargs}
    values.update(
        {
            key: value
            for key, value in {
                "topn": topn,
                "stocks": stocks,
                "start": start,
                "end": end,
            }.items()
            if value is not None
        }
    )
    return run_research(values)


__all__ = ["list_strategies", "run_backtest"]


if __name__ == "__main__":
    result = run_backtest()
    metrics = result.get("metrics", {})
    print(
        "tech3: 年化 {annual:.1%} 回撤 {drawdown:.1%} 夏普 {sharpe:.2f} 样本 {days} 天".format(
            annual=metrics.get("annual_return", 0.0),
            drawdown=metrics.get("max_drawdown", 0.0),
            sharpe=metrics.get("sharpe", 0.0),
            days=metrics.get("n_days", 0),
        )
    )
