"""Run analysis outside the HTTP server process."""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

from .db import Database
from .instance import library_lock
from .tasks import Tasks
from .worker import Worker


def _limited_mask(available_mask, max_cpu_cores):
    bits = [1 << index for index in range(available_mask.bit_length())
            if available_mask & (1 << index)]
    requested = int(max_cpu_cores)
    if requested < 0 or requested > len(bits):
        raise ValueError('CPU 核心数超出可用范围')
    selected = bits if requested == 0 else bits[:requested]
    return sum(selected), len(bits), len(selected)


def apply_cpu_limit(max_cpu_cores, *, available_mask=None):
    """Apply CPU affinity to this Windows worker and its current children."""
    requested = int(max_cpu_cores)
    if os.name != 'nt':
        available = sorted(os.sched_getaffinity(0))
        if requested < 0 or requested > len(available):
            raise ValueError('CPU 核心数超出可用范围')
        selected = available if requested == 0 else available[:requested]
        if not selected:
            raise ValueError('没有可用的 CPU 核心')
        os.sched_setaffinity(0, selected)
        return {'requested': requested, 'available': len(available),
                'applied': len(selected), 'processes': 1}

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.GetProcessAffinityMask.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                                                ctypes.POINTER(ctypes.c_size_t))
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.SetProcessAffinityMask.argtypes = (ctypes.c_void_p, ctypes.c_size_t)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    current = kernel32.GetCurrentProcess()
    process_mask, system_mask = ctypes.c_size_t(), ctypes.c_size_t()
    if available_mask is None:
        if not kernel32.GetProcessAffinityMask(current, ctypes.byref(process_mask),
                                                ctypes.byref(system_mask)):
            raise ctypes.WinError(ctypes.get_last_error())
        available_mask = process_mask.value
    selected_mask, available_count, selected_count = _limited_mask(available_mask, requested)

    class ProcessEntry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                    ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                    ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]

    kernel32.Process32FirstW.argtypes = (ctypes.c_void_p, ctypes.POINTER(ProcessEntry))
    kernel32.Process32NextW.argtypes = (ctypes.c_void_p, ctypes.POINTER(ProcessEntry))

    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    parents = {}
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)

    root_pid = os.getpid()
    descendants = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent in parents.items():
            if parent in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True
    applied = 0
    for pid in descendants:
        handle = kernel32.OpenProcess(0x0200 | 0x1000, False, pid)
        if not handle:
            continue
        try:
            if kernel32.SetProcessAffinityMask(handle, ctypes.c_size_t(selected_mask)):
                applied += 1
        finally:
            kernel32.CloseHandle(handle)
    return {'requested': requested, 'available': available_count,
            'applied': selected_count, 'processes': applied,
            '_available_mask': available_mask}


class WorkerClient:
    def __init__(self, db):
        self.db = db

    async def cancel(self, *, media_ids=None, run_id=None):
        return await self.command('cancel', media_ids=list(media_ids) if media_ids is not None else None,
                                  run_id=run_id)

    async def command(self, action, **payload):
        ident = str(uuid.uuid4())
        self.db.execute(
            'INSERT INTO worker_commands(id,action,payload,created) VALUES (?,?,?,?)',
            (ident, action, json.dumps(payload), time.time()),
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            row = await asyncio.to_thread(
                self.db.one, 'SELECT state,error FROM worker_commands WHERE id=?', (ident,)
            )
            if row and row['state'] in {'completed', 'failed'}:
                await asyncio.to_thread(self.db.execute, 'DELETE FROM worker_commands WHERE id=?', (ident,))
                if row['state'] == 'failed':
                    raise RuntimeError(row['error'] or '工作进程执行命令失败')
                return
            await asyncio.sleep(.1)
        raise RuntimeError('AI 工作进程没有响应；任务状态已保存，请稍后再试')

    def quota_state(self):
        row = self.db.one("SELECT value FROM worker_status WHERE key='quota'")
        return json.loads(row['value']) if row else {'state': 'unknown', 'limits': None}


async def _serve(directory, stop_event):
    db = Database(directory)
    worker = Worker(db, Tasks(db))
    available_mask = None
    with library_lock(db.directory, 'worker.lock'):
        db.restart_recovery()
        db.execute("UPDATE worker_commands SET state='pending' WHERE state='running'")
        db.execute("INSERT OR REPLACE INTO worker_status VALUES ('quota',?)",
                   (json.dumps(worker.quota_state),))

        async def commands():
            nonlocal available_mask
            current_cpu_limit = None
            while not stop_event.is_set():
                requested_cpu_limit = db.settings()['max_cpu_cores']
                if requested_cpu_limit != current_cpu_limit:
                    try:
                        status = apply_cpu_limit(requested_cpu_limit, available_mask=available_mask)
                        if os.name == 'nt' and available_mask is None:
                            available_mask = status.pop('_available_mask')
                        else:
                            status.pop('_available_mask', None)
                        db.execute("INSERT OR REPLACE INTO worker_status VALUES ('cpu',?)",
                                   (json.dumps(status),))
                        current_cpu_limit = requested_cpu_limit
                    except (OSError, ValueError) as exc:
                        db.execute("INSERT OR REPLACE INTO worker_status VALUES ('cpu',?)",
                                   (json.dumps({'requested': requested_cpu_limit,
                                                'error': type(exc).__name__}),))
                pending = db.rows("SELECT id,action,payload FROM worker_commands WHERE state='pending' ORDER BY created LIMIT 20")
                for row in pending:
                    if not db.execute("UPDATE worker_commands SET state='running' WHERE id=? AND state='pending'", (row['id'],)):
                        continue
                    try:
                        payload = json.loads(row['payload'])
                        if row['action'] == 'cancel':
                            await worker.cancel(media_ids=set(payload['media_ids']) if payload['media_ids'] is not None else None,
                                                run_id=payload['run_id'])
                        else:
                            raise ValueError('未知工作进程命令')
                        worker.settle()
                        db.execute("UPDATE worker_commands SET state='completed' WHERE id=?", (row['id'],))
                    except Exception as exc:
                        db.execute("UPDATE worker_commands SET state='failed',error=? WHERE id=?", (type(exc).__name__, row['id']))
                await asyncio.sleep(.1)

        runner = asyncio.create_task(worker.loop())
        controller = asyncio.create_task(commands())
        try:
            while not stop_event.is_set():
                if runner.done():
                    runner.result()
                await asyncio.sleep(.2)
        finally:
            controller.cancel()
            await asyncio.gather(controller, return_exceptions=True)
            await worker.close()
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)


def run_worker(directory, stop_event):
    if os.name == 'nt':
        # Favor the HTTP process when unrestricted analysis concurrency is busy.
        import ctypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetPriorityClass.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        kernel32.SetPriorityClass.restype = ctypes.c_int
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00004000)
    asyncio.run(_serve(directory, stop_event))
