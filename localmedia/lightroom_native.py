"""Lightroom-only native menu/dialog messages. No global mouse/keyboard input."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as W
import time
import json
from pathlib import Path
import subprocess


class NativeCatalogs:
    def __init__(self, session):
        self.session, self.user = session, session.user
        self.pid = session.pids()[0]
        self.backup_dialogs = set()
        self.user.GetSubMenu.argtypes = [W.HMENU, ctypes.c_int]
        self.user.GetSubMenu.restype = W.HMENU
        self.user.GetMenuStringW.argtypes = [W.HMENU, W.UINT, W.LPWSTR, ctypes.c_int, W.UINT]
        self.user.GetMenuItemID.argtypes = [W.HMENU, ctypes.c_int]
        self.user.GetMenuItemID.restype = W.UINT
        self.user.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
        self.user.GetDlgCtrlID.argtypes = [W.HWND]
        self.user.EnumChildWindows.argtypes = [W.HWND, session.callback_type, W.LPARAM]
        self.user.IsWindowVisible.argtypes = [W.HWND]
        self.user.SendMessageTimeoutW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM, W.UINT, W.UINT, ctypes.POINTER(ctypes.c_size_t)]
        self.user.SendMessageTimeoutW.restype = ctypes.c_size_t

    def check(self, window):
        owner = W.DWORD()
        self.user.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if owner.value != self.pid:
            raise RuntimeError('LR 窗口已变化，停止应用命令。')

    def send(self, window, message, wparam=0, lparam=0):
        self.check(window)
        result = ctypes.c_size_t()
        if not self.user.SendMessageTimeoutW(window, message, wparam, lparam, 2, 3000, ctypes.byref(result)):
            raise RuntimeError('LR 应用命令超时，请查看 LR 提示。')
        return result.value

    def post(self, window, message, wparam=0, lparam=0):
        self.check(window)
        if not self.user.PostMessageW(window, message, wparam, lparam):
            raise RuntimeError('无法提交 LR 应用命令。')

    def text(self, window):
        buffer = ctypes.create_unicode_buffer(1024)
        self.user.GetWindowTextW(window, buffer, len(buffer))
        return buffer.value.replace('&', '').strip()

    def class_name(self, window):
        buffer = ctypes.create_unicode_buffer(128)
        self.user.GetClassNameW(window, buffer, len(buffer))
        return buffer.value

    def children(self, window):
        found = []
        @self.session.callback_type
        def visit(child, _):
            found.append(child)
            return True
        self.user.EnumChildWindows(window, visit, 0)
        return found

    def main(self):
        windows = self.session.windows(self.pid, self.session.current())
        if len(windows) != 1:
            raise RuntimeError('无法唯一识别 LR 主窗口。')
        return windows[0]

    def backup_if_prompted(self):
        """Honor LR's scheduled backup; never skip it or change its destination."""
        found = []
        @self.session.callback_type
        def visit(window, _):
            owner = W.DWORD()
            self.user.GetWindowThreadProcessId(window, ctypes.byref(owner))
            if (owner.value == self.pid and self.user.IsWindowVisible(window)
                    and self.text(window) in ('备份目录', 'Back Up Catalog')):
                found.append(window)
            return True
        self.user.EnumWindows(visit, 0)
        for dialog in found:
            if dialog in self.backup_dialogs:
                continue
            buttons = [w for w in self.children(dialog) if self.class_name(w) == 'Button'
                       and self.text(w) in ('备份', 'Back Up') and self.user.IsWindowEnabled(w)]
            if len(buttons) == 1:
                self.post(buttons[0], 0xF5)
                self.backup_dialogs.add(dialog)

    def menu(self, top_labels, command_labels, plugin=False):
        deadline = time.monotonic() + 25
        while True:
            try:
                return self._menu(top_labels, command_labels, plugin)
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.3)

    def _menu(self, top_labels, command_labels, plugin=False):
        window = self.main()
        menu = self.user.GetMenu(window)
        def label(menu, index):
            buffer = ctypes.create_unicode_buffer(512)
            self.user.GetMenuStringW(menu, index, buffer, len(buffer), 0x400)
            return buffer.value.replace('&', '').strip()
        indexes = [i for i in range(self.user.GetMenuItemCount(menu)) if label(menu, i).startswith(top_labels)]
        if len(indexes) != 1:
            raise RuntimeError('找不到 LR 菜单。')
        submenu = self.user.GetSubMenu(menu, indexes[0])
        self.send(window, 0x117, submenu, indexes[0])
        try:
            if plugin:
                indexes = [i for i in range(self.user.GetMenuItemCount(submenu))
                           if label(submenu, i).startswith(('增效工具额外信息', 'Plug-in Extras', 'Plugin Extras'))]
                if len(indexes) != 1:
                    raise RuntimeError('LR 插件菜单尚未就绪。')
                submenu = self.user.GetSubMenu(submenu, indexes[0])
                self.send(window, 0x117, submenu, indexes[0])
            commands = [self.user.GetMenuItemID(submenu, i) for i in range(self.user.GetMenuItemCount(submenu))
                        if label(submenu, i).startswith(command_labels)]
            if len(commands) != 1 or commands[0] in (0, 0xFFFFFFFF):
                raise RuntimeError('找不到唯一的 LR 原生命令。')
        finally:
            self.send(window, 0x125, submenu)
        self.post(window, 0x111, commands[0])

    def dialog(self, labels, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            windows = []
            @self.session.callback_type
            def visit(window, _):
                owner = W.DWORD()
                self.user.GetWindowThreadProcessId(window, ctypes.byref(owner))
                if owner.value == self.pid:
                    windows.append(window)
                return True
            self.user.EnumWindows(visit, 0)
            candidates = [w for w in set(windows + self.children(self.main()))
                          if self.class_name(w) != 'AgWinMainFrame' and self.user.IsWindowVisible(w) and self.text(w).startswith(labels)]
            if len(candidates) == 1:
                return candidates[0]
            time.sleep(.1)
        raise RuntimeError('LR 对话框未出现，未继续操作。')

    def import_catalog(self, path):
        self.menu(('文件', 'File'), ('从另一个目录导入', 'Import from Another Catalog'))
        self.choose_catalog_file(path)
        # Lightroom's merge preview exposes real native combo boxes. Select by
        # displayed option text, never by an assumed index or a screen position.
        dialog = self.dialog(('从目录“', 'Import from Catalog', 'Lightroom Classic'), timeout=30)
        if self.class_name(dialog) == '#32770' and self.text(dialog) == 'Lightroom Classic':
            # Owner-drawn message text is available through this application's
            # accessibility provider, although GetWindowText cannot read it.
            self.check(dialog)
            script = ('[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); Add-Type -AssemblyName UIAutomationClient; '
                      f'$d=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]{int(dialog)}); '
                      '$n=$d.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition); '
                      '@($n | ForEach-Object { $_.Current.Name }) | ConvertTo-Json -Compress')
            result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', script],
                                    capture_output=True, check=True, timeout=10, creationflags=0x08000000)
            labels = json.loads(result.stdout.decode('utf-8-sig', errors='replace') or '[]')
            if not any('当前目录中的所有目录的照片都是最新的' in value or 'up to date' in value.lower() for value in labels):
                raise RuntimeError('LR 返回了未识别的导入提示，未继续操作。')
            buttons = [w for w in self.children(dialog) if self.class_name(w) == 'Button' and self.text(w) in ('确定', 'OK')]
            if len(buttons) != 1:
                raise RuntimeError('无法关闭 LR 已同步提示。')
            self.post(buttons[0], 0xF5)
            return
        children = self.children(dialog)
        self.user.GetParent.argtypes = [W.HWND]
        self.user.GetParent.restype = W.HWND
        replacements = 0
        for combo in (w for w in children if self.class_name(w) == 'ComboBox'):
            count = self.send(combo, 0x146)
            if count > 20:
                raise RuntimeError('未知的 LR 导入选项。')
            for index in range(count):
                text = ctypes.create_unicode_buffer(1024)
                self.send(combo, 0x148, index, ctypes.cast(text, ctypes.c_void_p).value)
                if text.value in ('仅元数据与修改照片设置', 'Metadata and develop settings only',
                                  '将新照片添加到目录而不移动', 'Add new photos to catalog without moving'):
                    self.send(combo, 0x14E, index)
                    self.send(self.user.GetParent(combo), 0x111,
                              (1 << 16) | (self.user.GetDlgCtrlID(combo) & 0xFFFF), combo)
                    if self.send(combo, 0x147) != index:
                        raise RuntimeError('无法确认仅同步目录信息，停止合并。')
                    replacements += 1
        if replacements != 2:
            raise RuntimeError('LR 导入选项变化，未提交合并。')
        copies = [w for w in children if self.text(w) in ('将旧设置保留为虚拟副本', 'Preserve old settings as a virtual copy')]
        if len(copies) != 1:
            raise RuntimeError('无法确认 LR 冲突副本选项。')
        if self.send(copies[0], 0xF0):
            self.send(copies[0], 0xF5)
        if self.send(copies[0], 0xF0):
            raise RuntimeError('无法取消冲突副本选项。')
        buttons = [w for w in children if self.class_name(w) == 'Button' and self.text(w) in ('导入', 'Import')]
        if len(buttons) != 1:
            raise RuntimeError('无法确认 LR 导入按钮。')
        self.post(buttons[0], 0xF5)

    def choose_catalog_file(self, path):
        dialog = self.dialog(('从 Lightroom 目录导入', 'Import from Lightroom Catalog'))
        children = self.children(dialog)
        edits = [w for w in children if self.class_name(w) == 'Edit' and self.user.GetDlgCtrlID(w) == 1148]
        buttons = [w for w in children if self.class_name(w) == 'Button' and self.user.GetDlgCtrlID(w) == 1]
        if len(edits) != 1 or len(buttons) != 1 or not path.is_file():
            raise RuntimeError('无法确认 LR 目录导入文件选择框。')
        value = ctypes.create_unicode_buffer(str(path))
        self.send(edits[0], 0xC, 0, ctypes.cast(value, ctypes.c_void_p).value)
        self.post(buttons[0], 0xF5)

    def export_selected(self, destination, allow_all=False, include_previews=False):
        if destination.exists():
            raise ValueError('导出目标已存在，不会覆盖。')
        self.menu(('文件', 'File'), ('导出为目录', 'Export as Catalog'))
        dialog = self.dialog(('导出为目录', 'Export as Catalog'))
        children = self.children(dialog)
        for labels, state in [(('仅导出选定照片', 'Export selected photos only'), 1),
                              (('导出负片文件', 'Export negative files'), 0),
                              (('构建/包括智能预览', 'Build / Include Smart Previews'), 0),
                              (('包括可用的预览', 'Include available previews'), int(include_previews))]:
            found = [w for w in children if self.class_name(w) == 'Button' and self.text(w) in labels]
            if not found and allow_all and labels[0] == '仅导出选定照片':
                # LR omits this control when the complete catalog is selected.
                # Only permit that layout if the caller verified zero hidden photos.
                continue
            if len(found) != 1:
                raise RuntimeError('LR 导出选项与受支持的界面不一致，停止导出。')
            if self.send(found[0], 0xF0) != state:
                self.send(found[0], 0xF5)
            if self.send(found[0], 0xF0) != state:
                raise RuntimeError('无法确认 LR 导出选项。')
        edits = [w for w in children if self.class_name(w) == 'Edit' and self.user.GetDlgCtrlID(w) == 1148]
        saves = [w for w in children if self.class_name(w) == 'Button' and self.user.GetDlgCtrlID(w) == 1]
        if len(edits) != 1 or len(saves) != 1:
            raise RuntimeError('无法确认 LR 导出路径输入框。')
        value = ctypes.create_unicode_buffer(str(destination))
        self.send(edits[0], 0xC, 0, ctypes.cast(value, ctypes.c_void_p).value)
        self.post(saves[0], 0xF5)
