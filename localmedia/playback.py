"""On-demand browser playback copies; source media is never modified."""
from __future__ import annotations

import hashlib
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .media import ffmpeg


class PlaybackCache:
    def __init__(self, directory):
        self.directory = Path(directory) / 'playback'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='playback')
        self.lock = threading.RLock()
        self.jobs = {}
        self.process = None
        self.closed = False

    def target(self, source):
        source = Path(source)
        stat = source.stat()
        key = hashlib.sha256(f'{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:h264-1080-v1'.encode()).hexdigest()
        return self.directory / f'{key}.mp4'

    def request(self, source):
        target = self.target(source)
        with self.lock:
            if target.is_file():
                return {'state': 'ready'}
            if self.closed:
                return {'state': 'failed', 'error': '播放缓存服务已关闭'}
            if target.name not in self.jobs:
                if sum(job['state'] in {'queued', 'preparing'} for job in self.jobs.values()) >= 2:
                    return {'state': 'queued'}
                self.jobs[target.name] = {'state': 'queued'}
                self.pool.submit(self._prepare, Path(source), target)
            return dict(self.jobs[target.name])

    def _prepare(self, source, target):
        temporary = target.with_suffix('.partial.mp4')
        try:
            for decoder, encoder, options in [
                (['-hwaccel', 'cuda'], 'h264_nvenc', ['-preset', 'p4', '-cq', '23']),
                ([], 'h264_nvenc', ['-preset', 'p4', '-cq', '23']),
                ([], 'h264_qsv', ['-preset', 'fast', '-global_quality', '23']),
                ([], 'libx264', ['-preset', 'veryfast', '-crf', '23']),
            ]:
                command = [ffmpeg(), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                           '-threads', '4', *decoder, '-i', str(source), '-map', '0:v:0', '-map', '0:a:0?',
                           '-vf', "scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,format=yuv420p",
                           '-c:v', encoder, *options, '-maxrate', '8M', '-bufsize', '16M',
                           '-g', '60', '-c:a', 'aac', '-b:a', '160k', '-ac', '2',
                           '-movflags', '+faststart', str(temporary)]
                with self.lock:
                    if self.closed:
                        return
                    self.jobs[target.name] = {'state': 'preparing', 'encoder': encoder}
                    self.process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    process = self.process
                _, error = process.communicate()
                with self.lock:
                    self.process = None
                    if self.closed:
                        return
                if process.returncode == 0:
                    if self.target(source) != target:
                        raise RuntimeError('源视频已改变，请重新打开')
                    os.replace(temporary, target)
                    with self.lock:
                        self.jobs[target.name] = {'state': 'ready', 'encoder': encoder}
                    return
            raise RuntimeError('无法生成流畅播放副本')
        except Exception as exc:
            with self.lock:
                self.jobs[target.name] = {'state': 'failed', 'error': str(exc)}
        finally:
            temporary.unlink(missing_ok=True)

    def close(self):
        with self.lock:
            self.closed = True
            if self.process and self.process.poll() is None:
                self.process.terminate()
        self.pool.shutdown(wait=True, cancel_futures=True)


class PlaybackFileResponseMixin:
    # Reduce async/threadpool overhead from the default 64 KiB chunks.
    chunk_size = 1024 * 1024
