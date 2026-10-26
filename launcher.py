# -*- coding: utf-8 -*-
"""QuantDeck 启动器：一键启动 量化 Deck（:8787）+ DeepSeek HARNESS（:3080）。

行为：
  - 数据目录：环境变量 LWQUANT_CACHE_DIR > EXE/仓库 同级 data/
  - HARNESS：若存在 harness/ 运行时且系统有 Node.js → 自动启动（DSH_HOME=harness/home）
    · 首次使用：把 harness/home/.credentials.yaml.example 复制为 .credentials.yaml
      并填入 DeepSeek API Key（先接 AI 的 API，AI 会协助你接入其余数据源 API）
    · 没有 Node.js 时跳过 HARNESS（量化系统照常运行）
  - 浏览器：自动打开 量化门户 http://127.0.0.1:8787（HARNESS GUI 为 3080，可手动打开）
"""
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path


def base_dir() -> Path:
    if getattr(sys, "frozen", False):          # PyInstaller 打包后
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent     # 源码运行：仓库根


def _builtin_python():
    """优先使用发布包内置便携 Python（runtime/python 或 portable-py）"""
    for c in (base_dir() / "runtime" / "python" / "python.exe",
              base_dir() / "portable-py" / "Scripts" / "python.exe"):
        if c.exists():
            return c
    return None


def _builtin_node():
    """优先使用发布包内置便携 Node（runtime/node 或 portable-node）"""
    for c in (base_dir() / "runtime" / "node" / "node.exe",
              base_dir() / "portable-node" / "node.exe"):
        if c.exists():
            return c
    return None


def start_harness(base: Path):
    """启动 DeepSeek HARNESS（harness/ 存在且 Node.js 可用时）。返回进程或 None。"""
    node = _builtin_node() or shutil.which("node")
    harness_root = base / "harness"
    dsh_bin = harness_root / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
    if not node:
        print("[QuantDeck] 未检测到 Node.js —— HARNESS 对话功能跳过（量化系统照常）")
        return None
    try:
        node_version = subprocess.check_output([str(node), "--version"], text=True, timeout=10).strip()
        if int(node_version.lstrip("v").split(".")[0]) < 24:
            print(f"[QuantDeck] Node.js {node_version} 过旧 —— 本项目 HARNESS 需要 Node.js 24+，对话功能跳过")
            return None
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"[QuantDeck] 无法检查 Node.js 版本 —— HARNESS 对话功能跳过: {exc}")
        return None
    if not dsh_bin.exists():
        print("[QuantDeck] harness 运行时缺失 —— 请先运行 harness/install.cmd 安装 HARNESS")
        return None
    cred = harness_root / "home" / ".credentials.yaml"
    if not cred.exists():
        print("[QuantDeck] HARNESS 未配置 API Key：把 harness/home/.credentials.yaml.example "
              "复制为 .credentials.yaml 并填入 DeepSeek API Key（见 docs/HARNESS接入.md）")
    env = dict(os.environ)
    env["DSH_HOME"] = str(harness_root / "home")
    print(f"[QuantDeck] 启动 DeepSeek HARNESS（DSH_HOME={env['DSH_HOME']}）...")
    try:
        proc = subprocess.Popen([str(node), str(dsh_bin), "web"],
                                cwd=str(harness_root), env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return proc
    except Exception as e:
        print(f"[QuantDeck] HARNESS 启动失败: {e}")
        return None


def _open_browsers():
    time.sleep(2.0)
    try:
        webbrowser.open("http://127.0.0.1:8787")
    except Exception:
        pass


def main():
    base = base_dir()
    if not getattr(sys, "frozen", False):
        python = _builtin_python() or base / ".venv" / "Scripts" / "python.exe"
        if (python.exists() and Path(sys.executable).resolve() != python.resolve()
                and Path(sys.prefix).resolve() != python.parent.parent.resolve()):
            return subprocess.call([str(python), str(base / "launcher.py"), *sys.argv[1:]], cwd=base)
    os.chdir(base)
    from data.cache import CACHE_DIR
    data_dir = str(CACHE_DIR)
    print(f"[QuantDeck] 数据目录: {data_dir}")
    if not (Path(data_dir) / "bars.db").exists():
        print("[QuantDeck] 提示: 未检测到 bars.db —— 请先获取数据（docs/快速开始.md §3），或使用演示数据模式")

    from deck.deck_server import Handler, PORT, ResearchHTTPServer, RESEARCH_LOCK
    # 先占端口再启动其他服务：端口已被旧进程占用时直接说明，不会和旧版本共用端口。
    try:
        srv = ResearchHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"[QuantDeck] 端口 {PORT} 已被占用：平台可能已在运行，或上次的窗口还没关闭。"
              f"请先关闭旧窗口再启动；直接打开 http://127.0.0.1:{PORT} 可使用正在运行的平台。")
        return 1

    start_harness(base)                 # HARNESS（可选，:3080）
    threading.Thread(target=_open_browsers, daemon=True).start()

    print(f"[QuantDeck] 量化系统已启动: http://127.0.0.1:{PORT}  （Ctrl+C 退出）")
    from data.market_dashboard import startup
    startup()
    from backtest.research_automation import start_scheduler
    start_scheduler(RESEARCH_LOCK)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    sys.exit(main())
