"""Read-only architecture checks used by ``main.py validate``.

The audit deliberately checks boundaries instead of trying to lint every
feature.  It catches the regressions that make the workspace grow a second
frontend or a second research engine: missing configuration templates,
duplicate API branches, static pages that bypass ``LW.api`` and an old
backtest implementation being reintroduced beside ``research_runner``.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re


def _check(ok, checks, name, detail, *, warning=False):
    item = {"name": name, "detail": detail, "ok": bool(ok)}
    checks.append(item)
    return item


def audit_workspace(base: str | Path) -> dict:
    root = Path(base).resolve()
    checks: list[dict] = []
    warnings: list[str] = []
    errors: list[str] = []

    py_files = [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]
    syntax_errors = []
    for path in py_files:
        try:
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            syntax_errors.append(f"{path.relative_to(root)}: {exc}")
    _check(not syntax_errors, checks, "python_syntax", "全部 Python 文件可解析" if not syntax_errors else syntax_errors)
    errors.extend(syntax_errors)

    pages = root / "ui_v2" / "pages"
    required_pages = ("research.html", "factors.html", "backtests.html", "market.html")
    missing = [name for name in required_pages if not (pages / name).exists()]
    _check(not missing, checks, "unified_studio_pages", "页面齐全" if not missing else missing)
    errors.extend(f"缺少统一工作台页面：{name}" for name in missing)

    api_source = root / "deck" / "research_api.py"
    source = api_source.read_text(encoding="utf-8") if api_source.exists() else ""
    # GET and POST may intentionally expose the same resource with different
    # verbs.  Check duplicate branches inside each handler separately.
    paths = []
    try:
        tree = ast.parse(source, filename=str(api_source))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {"get", "post"}:
                body = ast.get_source_segment(source, node) or ""
                paths.append((node.name, re.findall(r"(?:path\s*==|path\.startswith\()\s*['\"]([^'\"]+)", body)))
    except SyntaxError:
        paths = []
    duplicates = sorted({p for _, items in paths for p in items if items.count(p) > 1})
    duplicates = sorted({p for p in paths if paths.count(p) > 1})
    _check(not duplicates, checks, "api_route_uniqueness", "研究 API 路由无重复分支" if not duplicates else duplicates)
    errors.extend(f"研究 API 路由重复：{p}" for p in duplicates)
    _check("/api/studio/config" in source and "/api/studio/technical-rules" in source,
           checks, "research_api_routes", "统一研究 API 入口存在")

    config_dir = root / "config"
    missing_examples = []
    if config_dir.exists():
        for path in config_dir.glob("*.yaml"):
            if not path.with_suffix(".yaml.example").exists():
                missing_examples.append(path.name)
    _check(not missing_examples, checks, "config_templates",
           "每个本地 YAML 都有开源模板" if not missing_examples else missing_examples)
    warnings.extend(f"配置缺少 .example 模板：{name}" for name in missing_examples)

    frontend_files = list((root / "ui_v2" / "pages").glob("*.html"))
    bypasses = []
    for path in frontend_files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        # Static business data is allowed in CSS and labels; flag JS arrays/objects
        # only when a page has no API client at all.
        inline_business = re.search(r"(?:factor_catalog|strategy|stock_pools|stock-list)\s*[:=]", text)
        if inline_business and "LW.api" not in text:
            bypasses.append(str(path.relative_to(root)))
    _check(not bypasses, checks, "frontend_api_boundary",
           "业务页面通过 LW.api 获取动态数据" if not bypasses else bypasses)
    warnings.extend(f"页面可能绕过 LW.api：{name}" for name in bypasses)

    bt = root / "backtest" / "bt_runner.py"
    bt_text = bt.read_text(encoding="utf-8") if bt.exists() else ""
    forwarding = "research_runner" in bt_text and "def run_backtest" in bt_text
    _check(forwarding, checks, "single_backtest_engine",
           "bt_runner 仅作为 research_runner 兼容入口" if forwarding else "未检测到兼容转发层")
    if not forwarding:
        errors.append("回测入口可能重新出现第二套实现")

    result = {
        "ok": not errors,
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
    }
    return json.loads(json.dumps(result, ensure_ascii=False))


__all__ = ["audit_workspace"]
