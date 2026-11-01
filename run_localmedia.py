"""Serve the gallery with analysis in a separate process."""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import threading
import webbrowser
from pathlib import Path


def allowed_hosts_for_binding(host):
    hosts = {'localhost', '127.0.0.1', '[::1]', 'testserver'}
    if host not in {'0.0.0.0', '::'}:
        hosts.add(host)
        return sorted(hosts)
    hosts.update({socket.gethostname(), socket.getfqdn()})
    try:
        hosts.update(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('8.8.8.8', 80))
            hosts.add(probe.getsockname()[0])
    except OSError:
        pass
    return sorted(hosts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=18765)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--build', action='store_true')
    parser.add_argument('--data', default=None)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    os.chdir(root)
    static = root / 'localmedia/static'
    if args.build or not (static / 'index.html').is_file():
        npm = shutil.which('npm.cmd') or shutil.which('npm')
        if not npm:
            raise SystemExit('首次启动需要 Node.js，请安装后重试。')
        subprocess.run([npm, 'install'], cwd=root / 'frontend', check=True)
        subprocess.run([npm, 'run', 'build:localmedia'], cwd=root / 'frontend', check=True)
    from localmedia.api import create_app
    import uvicorn
    app = create_app(args.data, allowed_hosts=allowed_hosts_for_binding(args.host),
                     separate_worker=True)
    if not args.no_browser:
        def open_when_ready():
            import time
            import urllib.request
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _ in range(100):
                try:
                    with opener.open(f'http://127.0.0.1:{args.port}/api/health', timeout=1) as response:
                        if response.status == 200:
                            webbrowser.open(f'http://127.0.0.1:{args.port}')
                            return
                except OSError:
                    time.sleep(.2)
        threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == '__main__':
    main()
