from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    frontend_dir = root / "frontend"
    npm = "npm.cmd" if os.name == "nt" else "npm"

    node_path = shutil.which("node")
    npm_path = shutil.which(npm)
    if node_path is None or npm_path is None:
        print("未找到 Node.js 或 npm，前端无法启动。", file=sys.stderr)
        print("解决方式二选一：", file=sys.stderr)
        print("1. 在当前 conda 环境安装：conda install -c conda-forge nodejs", file=sys.stderr)
        print("2. 把 Node.js 安装目录加入 IDE/PyCharm 的 PATH，例如 E:\\系统应用\\nodejs", file=sys.stderr)
        print(f"当前 node: {node_path or 'not found'}", file=sys.stderr)
        print(f"当前 npm: {npm_path or 'not found'}", file=sys.stderr)
        return 1

    return subprocess.call([npm, "run", "dev", "--", "--port", "5173"], cwd=frontend_dir)


if __name__ == "__main__":
    raise SystemExit(main())
