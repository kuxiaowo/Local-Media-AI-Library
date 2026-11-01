"""Run the native folder dialog in its own GUI process."""
import asyncio
import json
import sys
from pathlib import Path


async def choose_folder(initial=''):
    process = await asyncio.create_subprocess_exec(
        sys.executable, str(Path(__file__).resolve()), initial,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, _ = await process.communicate()
        if process.returncode:
            raise RuntimeError('无法打开本机目录选择框，请手动填写路径')
        return json.loads(stdout)['path']
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


if __name__ == '__main__':
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        initial = sys.argv[1] if len(sys.argv) > 1 else ''
        path = filedialog.askdirectory(parent=root, title='选择媒体目录',
                                       initialdir=initial or None, mustexist=True)
        print(json.dumps({'path': path or None}, ensure_ascii=True))
    finally:
        root.destroy()
