"""Per-catalog AI services and a small one-click catalog switch panel."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from localmedia.lightroom import CatalogPair, WindowsLightroom, switch_catalog


def ready(config, mode):
    item = config['ai'][mode]
    request = urllib.request.Request(f"http://127.0.0.1:{item['port']}/api/lightroom/health", headers={
        'X-LocalMedia-Token': item['token'], 'X-LocalMedia-Catalog': item['catalog_id'],
    })
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=1) as response:
            return response.read().decode() == 'ok\n' + item['catalog_id']
    except urllib.error.HTTPError:
        raise RuntimeError('AI 端口被其他服务占用，请更换配置中的端口。') from None
    except (OSError, urllib.error.URLError):
        return False


def start_service(path, config, mode):
    if ready(config, mode):
        return
    item = config['ai'][mode]
    directory = Path(item['data'])
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'service.log').open('ab') as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--config', str(path),
                                    '--serve', mode], cwd=ROOT, stdout=log, stderr=log,
                                   creationflags=0x08000000)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if ready(config, mode):
            return
        if process.poll() is not None:
            raise RuntimeError('AI 服务启动失败，请检查该图库的 service.log。')
        time.sleep(.2)
    raise RuntimeError('AI 服务仍未就绪，请检查该图库的 service.log。')


def panel(path, config, pair):
    import tkinter as tk
    from tkinter import ttk
    app = tk.Tk()
    app.title('LocalMedia · 图库切换')
    app.geometry('380x225')
    status = tk.StringVar(value='选择要显示的图库')
    ttk.Label(app, textvariable=status, wraplength=345).pack(pady=16)
    buttons = []

    def action(mode):
        for button in buttons:
            button.config(state='disabled')
        status.set('正在正常关闭并切换 Lightroom…')
        def work():
            try:
                session = WindowsLightroom(pair)
                with session.exclusive():
                    result = switch_catalog(session, getattr(pair, mode))
                text = ('普通图库已显示' if mode == 'normal' else '独立图库已显示') + f" · {result['seconds']:.1f} 秒"
            except Exception as exc:
                text = str(exc)
            def finish():
                status.set(text)
                for button in buttons:
                    button.config(state='normal')
            app.after(0, finish)
        threading.Thread(target=work, daemon=True).start()
    for title, mode in [('隐藏独立图库', 'normal'), ('显示独立图库', 'alternate')]:
        button = ttk.Button(app, text=title, command=lambda m=mode: action(m))
        button.pack(fill='x', padx=32, pady=5)
        buttons.append(button)
    ttk.Label(app, text='AI 功能在 Lightroom 的“增效工具额外信息”中').pack(pady=12)
    app.mainloop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True, type=Path)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--serve', choices=['normal', 'alternate'])
    action.add_argument('--ai', choices=['normal', 'alternate'])
    action.add_argument('--switch', choices=['normal', 'alternate'])
    parser.add_argument('--detach', action='store_true')
    args = parser.parse_args()
    path = args.config.resolve()
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    pair = CatalogPair.load(path)
    if args.detach:
        if not args.switch:
            parser.error('--detach requires --switch')
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--config', str(path),
                          '--switch', args.switch], cwd=ROOT, creationflags=0x08000000)
        return
    if args.serve:
        from localmedia.api import create_app
        import uvicorn
        item = config['ai'][args.serve]
        uvicorn.run(create_app(item['data'], lightroom=item), host='127.0.0.1', port=item['port'], access_log=False)
    elif args.ai:
        start_service(path, config, args.ai)
    elif args.switch:
        result = {'ok': False}
        try:
            session = WindowsLightroom(pair)
            with session.exclusive():
                result = switch_catalog(session, getattr(pair, args.switch))
        except Exception as exc:
            result['error'] = str(exc)
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(exc), 'LocalMedia 图库切换未完成', 0x10)
        (path.parent / 'last-switch.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    else:
        panel(path, config, pair)


if __name__ == '__main__':
    main()
