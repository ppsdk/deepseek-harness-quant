# -*- coding: utf-8 -*-
"""DSHQuant 命令行入口。

命令行和网页必须共用同一套配置、数据和研究服务。本入口只负责解析命令并
转发到已有模块，避免再维护一套“开发中”骨架逻辑。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

BASE_DIR = Path(__file__).resolve().parent


def _load_params() -> dict:
    """读取用户配置；开源检出只有模板时也能显示帮助和运行审计。"""
    import yaml

    for name in ("params.yaml", "params.yaml.example"):
        path = BASE_DIR / "config" / name
        if path.exists():
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DeepSeek HARNESS Quant 研究工作区")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("update", help="运行每日数据增量管道")
    sub.add_parser("validate", help="运行核心研究验证")
    sub.add_parser("screen", help="按策略生成收盘候选排名")
    sub.add_parser("backtest", help="运行标准研究回测")
    sub.add_parser("report", help="输出当前工作区架构审计报告")
    return parser


def _run(args: argparse.Namespace) -> int:
    if args.command == "update":
        from data.daily_pipeline import main as pipeline_main

        pipeline_main()
        return 0
    if args.command == "validate":
        from validation.workspace_audit import audit_workspace

        result = audit_workspace(BASE_DIR)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    if args.command == "screen":
        from backtest.research_workspace import screen

        print(json.dumps(screen({}), ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "backtest":
        from backtest.research_runner import run_research

        print(json.dumps(run_research({}), ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "report":
        from validation.workspace_audit import audit_workspace

        print(json.dumps(audit_workspace(BASE_DIR), ensure_ascii=False, indent=2))
        return 0
    cfg = _load_params()
    sources = ((cfg.get("data") or {}).get("sources") or {})
    print("DSHQuant 研究工作区")
    print("配置已加载：{}；可用命令：update / validate / screen / backtest / report".format(
        ", ".join(str(v) for v in sources.values()) or "模板配置"
    ))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return _run(_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
