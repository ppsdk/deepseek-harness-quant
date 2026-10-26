# -*- coding: utf-8 -*-
"""在本机隐藏输入当前默认模型提供商的 API Key。"""
import shutil
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def main():
    candidates = [BASE / "runtime/node/node.exe", BASE / "portable-node/node.exe"]
    node = next((str(p) for p in candidates if p.exists()), None) or shutil.which("node")
    if not node:
        raise SystemExit("未检测到 Node.js 24+，请先安装 Node.js。")
    result = subprocess.run([node, str(BASE / "harness/setup-key.cjs")], cwd=BASE)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
