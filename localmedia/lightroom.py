"""Background, application-scoped Lightroom catalog switching on Windows.

No mouse, keyboard injection, screenshots, encryption, or administrator access.
Only the configured executable and catalogs may be opened or normally closed.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
import ntpath
import os
from pathlib import Path
import subprocess
import time


def lock_belongs_to_process(content: str, executable: str, pid: int) -> bool:
    """LR's lock file contains its executable path and PID on separate lines."""
    lines = content.lstrip("\ufeff").splitlines()
    if len(lines) != 2:
        return False
    try:
        return (int(lines[1]) == pid
                and ntpath.normcase(ntpath.normpath(lines[0]))
                == ntpath.normcase(ntpath.normpath(executable)))
    except ValueError:
        return False


@dataclass(frozen=True)
class CatalogPair:
    executable: Path
    normal: Path
    alternate: Path

    @classmethod
    def load(cls, path: Path):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        pair = cls(*(Path(data[key]).resolve() for key in ("lightroom_executable", "normal_catalog", "alternate_catalog")))
        pair.validate()
        return pair

    def validate(self):
        if self.executable.name.lower() != "lightroom.exe" or not self.executable.is_file():
            raise ValueError("请配置有效的 Lightroom.exe。")
        if self.normal == self.alternate:
            raise ValueError("两个图库必须位于不同路径。")
        for catalog in (self.normal, self.alternate):
            if catalog.suffix.lower() != ".lrcat" or not catalog.is_file():
                raise ValueError("配置的 LR 图库文件不存在。")


class WindowsLightroom:
    def __init__(self, pair: CatalogPair):
        if os.name != "nt":
            raise RuntimeError("此切换器目前只支持 Windows。")
        pair.validate()
        self.pair = pair
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user.IsHungAppWindow.argtypes = [wintypes.HWND]
        self.user.IsWindowEnabled.argtypes = [wintypes.HWND]
        self.user.GetMenu.argtypes = [wintypes.HWND]
        self.user.GetMenu.restype = wintypes.HMENU
        self.user.GetMenuItemCount.argtypes = [wintypes.HMENU]
        self.user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self.user.EnumWindows.argtypes = [self.callback_type, wintypes.LPARAM]

    @contextlib.contextmanager
    def exclusive(self):
        mutex = self.kernel.CreateMutexW(None, False, "Local\\LocalMedia.LightroomCatalogSwitch")
        if not mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = False
        try:
            acquired = self.kernel.WaitForSingleObject(mutex, 0) in (0, 128)
            if not acquired:
                raise RuntimeError("另一个图库切换正在进行，请稍候。")
            yield
        finally:
            if acquired:
                self.kernel.ReleaseMutex(mutex)
            self.kernel.CloseHandle(mutex)

    def pids(self):
        tasklist = str(Path(os.environ["SystemRoot"]) / "System32" / "tasklist.exe")
        result = subprocess.run([tasklist, "/FI", "IMAGENAME eq Lightroom.exe", "/FO", "CSV", "/NH"],
                                capture_output=True, check=True, creationflags=0x08000000)
        rows = csv.reader(result.stdout.decode("mbcs", errors="replace").splitlines())
        return [int(row[1]) for row in rows if len(row) > 1 and row[0].casefold() == "lightroom.exe"]

    def process(self, pid):
        handle = self.kernel.OpenProcess(0x1000 | 0x100000, False, pid)
        if not handle:
            raise RuntimeError("无法检查 LR 进程。")
        executable = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(executable))
        if not self.kernel.QueryFullProcessImageNameW(handle, 0, executable, ctypes.byref(length)):
            self.kernel.CloseHandle(handle)
            raise RuntimeError("无法核对 LR 程序路径。")
        if ntpath.normcase(executable.value) != ntpath.normcase(str(self.pair.executable)):
            self.kernel.CloseHandle(handle)
            raise RuntimeError("正在运行的 Lightroom 不是配置的程序。")
        return handle

    def windows(self, pid, catalog):
        found = []

        @self.callback_type
        def visit(window, _):
            owner = wintypes.DWORD()
            self.user.GetWindowThreadProcessId(window, ctypes.byref(owner))
            if owner.value == pid:
                # LR can replace its catalog title with just "Lightroom Classic".
                # Identity is verified from the executable + live catalog lock;
                # the populated native menu identifies its main application window.
                menu = self.user.GetMenu(window)
                if menu and self.user.GetMenuItemCount(menu) >= 4:
                    found.append(window)
            return True
        self.user.EnumWindows(visit, 0)
        return found

    def catalog_for_pid(self, pid, handle):
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not self.kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                           ctypes.byref(kernel), ctypes.byref(user)):
            raise RuntimeError("无法核对 LR 会话创建时间。")
        created_at = ((created.dwHighDateTime << 32) | created.dwLowDateTime) / 10_000_000 - 11644473600
        candidates = []
        for catalog in (self.pair.normal, self.pair.alternate):
            lock = Path(str(catalog) + ".lock")
            try:
                if lock.stat().st_mtime < created_at - 2:
                    continue
                if lock_belongs_to_process(lock.read_text(encoding="utf-8"), str(self.pair.executable), pid):
                    candidates.append(catalog)
            except (OSError, UnicodeError):
                continue
        return candidates[0] if len(candidates) == 1 else None

    def current(self):
        pids = self.pids()
        if not pids:
            return None
        if len(pids) != 1:
            raise RuntimeError("发现多个 Lightroom 进程，拒绝猜测目标。")
        handle = self.process(pids[0])
        try:
            catalog = self.catalog_for_pid(pids[0], handle)
            if catalog is None:
                raise RuntimeError("当前打开的不是配置的两个图库，不会关闭它。")
            return catalog
        finally:
            self.kernel.CloseHandle(handle)

    def close(self, catalog, timeout=35):
        if catalog not in (self.pair.normal, self.pair.alternate):
            raise ValueError("只能关闭已配置的图库。")
        pids = self.pids()
        if len(pids) != 1:
            raise RuntimeError("LR 会话已经变化。")
        handle = self.process(pids[0])
        try:
            if self.catalog_for_pid(pids[0], handle) != catalog:
                raise RuntimeError("LR 图库已经变化。")
            windows = self.windows(pids[0], catalog)
            if len(windows) != 1:
                raise RuntimeError("无法唯一识别目标 LR 窗口。")
            if not self.user.PostMessageW(windows[0], 0x0010, 0, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            if self.kernel.WaitForSingleObject(handle, int(timeout * 1000)) != 0:
                raise RuntimeError("LR 正在等待保存、备份或退出确认；未强制关闭。")
        finally:
            self.kernel.CloseHandle(handle)

    def open(self, catalog, timeout=40):
        if catalog not in (self.pair.normal, self.pair.alternate):
            raise ValueError("只能打开已配置的图库。")
        if self.pids():
            raise RuntimeError("已有 LR 会话，拒绝把路径交给未知进程。")
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 4  # SW_SHOWNOACTIVATE; no focus / global input manipulation.
        process = subprocess.Popen([str(self.pair.executable), str(catalog)],
                                   cwd=self.pair.executable.parent, startupinfo=startup)
        handle = self.process(process.pid)
        stable = 0
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("LR 提前退出，目标图库尚未打开。")
                windows = self.windows(process.pid, catalog)
                ready = (len(windows) == 1 and self.user.IsWindowEnabled(windows[0])
                         and not self.user.IsHungAppWindow(windows[0])
                         and self.catalog_for_pid(process.pid, handle) == catalog)
                stable = stable + 1 if ready else 0
                if stable >= 3:
                    return
                time.sleep(0.1)
            raise RuntimeError("目标图库未在时限内打开，请查看 LR 自身提示。")
        finally:
            self.kernel.CloseHandle(handle)


def switch_catalog(session, target: Path, before_open=lambda: None):
    """Injectable transition used by the CLI and by meaningful fault tests."""
    if target not in (session.pair.normal, session.pair.alternate):
        raise ValueError("目标图库未配置。")
    started = time.perf_counter()
    current = session.current()
    if current == target:
        return {"ok": True, "already_open": True, "seconds": 0.0}
    report = {"ok": False, "already_open": False}
    t = time.perf_counter()
    if current is not None:
        session.close(current)
    report["close_seconds"] = time.perf_counter() - t
    before_open()
    t = time.perf_counter()
    session.open(target)
    report["open_seconds"] = time.perf_counter() - t
    report["seconds"] = time.perf_counter() - started
    report["ok"] = True
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--target", required=True, choices=("normal", "alternate"))
    args = parser.parse_args()
    result_file = args.config.parent / "last-switch.json"
    try:
        pair = CatalogPair.load(args.config)
        session = WindowsLightroom(pair)
        with session.exclusive():
            result = switch_catalog(session, getattr(pair, args.target))
        result["target"] = args.target
    except Exception as exc:
        result = {"ok": False, "target": args.target, "error": str(exc)}
    result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
