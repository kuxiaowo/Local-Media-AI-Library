from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .media import IMAGES, VIDEOS, file_hash, make_preview, normalized, sort_timestamp


class Scanner:
    def __init__(self, db):
        self.db = db
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.interrupt_scan = threading.Event()
        self.lock = threading.Lock()
        self.preview_lock = threading.Lock()
        self.preview_active = threading.Event()
        self.observer = None
        self.preview_wake = threading.Event()
        self.hash_wake = threading.Event()

    def start(self):
        self.thread = threading.Thread(target=self.loop, daemon=True, name='media-sync')
        self.thread.start()
        self.preview_thread = threading.Thread(target=self.preview_loop, daemon=True, name='media-preview')
        self.preview_thread.start()
        self.hash_thread = threading.Thread(target=self.hash_loop, daemon=True, name='media-hash')
        self.hash_thread.start()
        self.watch()

    def watch(self):
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=3)
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
            scanner = self
            class Handler(FileSystemEventHandler):
                def on_any_event(self, event):
                    if event.event_type in {'created', 'deleted', 'modified', 'moved'}:
                        scanner.wake.set()
            self.observer = Observer()
            for root in self.db.enabled_roots():
                if Path(root['path']).is_dir():
                    self.observer.schedule(Handler(), root['path'], recursive=True)
            self.observer.start()
        except (ImportError, OSError):
            self.db.event('目录监听不可用，启动扫描及每 15 分钟补扫仍有效')

    def loop(self):
        while not self.stop.is_set():
            self.wake.clear()
            try:
                self.scan()
            except Exception as exc:
                self.db.event('扫描失败：' + type(exc).__name__)
            self.wake.wait(900)
            if self.wake.is_set():
                self.stop.wait(1)  # coalesce bursts from directory moves

    def close(self):
        self.stop.set()
        self.wake.set()
        self.preview_wake.set()
        self.hash_wake.set()
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=3)
        if hasattr(self, 'thread'):
            self.thread.join(timeout=5)
        if hasattr(self, 'preview_thread'):
            self.preview_thread.join(timeout=65)
        if hasattr(self, 'hash_thread'):
            self.hash_thread.join(timeout=5)

    def preview_loop(self):
        while not self.stop.is_set():
            self.cleanup_previews()
            self.preview_wake.clear()
            while not self.stop.is_set():
                self.cleanup_previews()
                root_ids = [root['id'] for root in self.db.enabled_roots()]
                rows = self.db.rows(
                    "SELECT * FROM media WHERE availability='online' AND preview_state='pending' "
                    "AND size IS NOT NULL AND mtime_ns IS NOT NULL "
                    "AND root_id IN (" + ','.join('?' for _ in root_ids) + ") LIMIT 32",
                    root_ids,
                ) if root_ids else []
                if not rows:
                    break
                handled = False
                for row in rows:
                    if self.stop.is_set():
                        return
                    if time.time_ns() - row['mtime_ns'] < 2_000_000_000:
                        continue
                    with self.preview_lock:
                        if self.db.one('SELECT id FROM media WHERE id=?',(row['id'],)):
                            self.preview_active.set()
                            try:
                                self.preview(row)
                            finally:
                                self.preview_active.clear()
                            handled = True
                if not handled:
                    break
            self.preview_wake.wait(10)

    def hash_loop(self):
        """Fill content hashes after previews, outside the latency-sensitive scan."""
        while not self.stop.is_set():
            self.hash_wake.clear()
            while not self.stop.is_set():
                root_ids = [root['id'] for root in self.db.enabled_roots()]
                if not root_ids:
                    break
                pending_preview = self.db.one(
                    "SELECT 1 FROM media WHERE availability='online' AND preview_state='pending' "
                    "AND size IS NOT NULL AND mtime_ns IS NOT NULL "
                    "AND root_id IN (" + ','.join('?' for _ in root_ids) + ") LIMIT 1", root_ids)
                if pending_preview:
                    self.preview_wake.set()
                    if self.stop.wait(.25):
                        return
                    continue
                row = self.db.one(
                    "SELECT * FROM media WHERE availability='online' AND hash IS NULL "
                    "AND root_id IN (" + ','.join('?' for _ in root_ids) + ") ORDER BY updated,id LIMIT 1",
                    root_ids,
                )
                if not row:
                    self.reconcile_pending_identities()
                    break
                if not self.hash_one(row):
                    break
            self.hash_wake.wait(10)

    def hash_one(self, row):
        path = Path(row['path'])
        try:
            stat = path.stat()
            if time.time_ns() - stat.st_mtime_ns < 2_000_000_000:
                return False
            if (stat.st_size, stat.st_mtime_ns) != (row['size'], row['mtime_ns']):
                self.wake.set()
                return False
            digest = file_hash(path, self.hash_interrupted)
            after = path.stat()
            if (after.st_size, after.st_mtime_ns) != (row['size'], row['mtime_ns']):
                self.wake.set()
                return False
        except InterruptedError:
            return False
        except OSError:
            self.wake.set()
            return False
        with self.db.connect() as db:
            current = db.execute(
                "SELECT * FROM media WHERE id=? AND availability='online' AND hash IS NULL",
                (row['id'],),
            ).fetchone()
            if not current or (current['size'], current['mtime_ns']) != (row['size'], row['mtime_ns']):
                return True
            db.execute('UPDATE media SET hash=? WHERE id=?', (digest, row['id']))
            db.execute("UPDATE tasks SET source_hash=? WHERE media_id=? AND source_hash IS NULL AND state='queued'",
                       (digest, row['id']))
        self.reconcile_identity(digest, row['size'])
        return True

    def hash_interrupted(self):
        while self.preview_active.is_set() and not self.stop.is_set():
            self.stop.wait(.05)
        return self.stop.is_set()

    def drain_hashes(self):
        """Synchronously finish pending hashes; intended for maintenance and tests."""
        while True:
            row = self.db.one("SELECT * FROM media WHERE availability='online' AND hash IS NULL ORDER BY updated,id LIMIT 1")
            if not row or not self.hash_one(row):
                return

    def reconcile_identity(self, digest, size):
        # Wait until same-sized arrivals have hashes so duplicate copies cannot
        # be mistaken for a single cross-volume move.
        if self.db.one("SELECT 1 FROM media WHERE availability='online' AND identity_pending=1 AND hash IS NULL AND size=? LIMIT 1", (size,)):
            return
        arrivals = self.db.rows(
            "SELECT * FROM media WHERE availability='online' AND identity_pending=1 AND hash=?", (digest,))
        missing = self.db.rows(
            "SELECT * FROM media WHERE availability='missing' AND identity_pending=0 AND hash=?", (digest,))
        if not missing:
            if arrivals and not self.db.one("SELECT 1 FROM roots WHERE state='scanning' LIMIT 1"):
                self.db.execute(
                    "UPDATE media SET identity_pending=0 WHERE hash=? AND availability='online'", (digest,))
            return
        if len(arrivals) != 1 or len(missing) != 1:
            if arrivals:
                self.db.execute("UPDATE media SET identity_pending=0 WHERE hash=? AND availability='online'", (digest,))
            return
        new, old = arrivals[0], missing[0]
        with self.db.connect() as db:
            db.execute('UPDATE tasks SET media_id=?,source_hash=COALESCE(source_hash,?) WHERE media_id=?',
                       (old['id'], digest, new['id']))
            db.execute('DELETE FROM media WHERE id=?', (new['id'],))
            db.execute(
                "UPDATE media SET root_id=?,path=?,parent=?,name=?,fsid=?,size=?,mtime_ns=?,hash=?,"
                "availability='online',preview_state=?,preview=?,metadata=?,sort_time=?,identity_pending=0,error=?,updated=? WHERE id=?",
                (new['root_id'], new['path'], new['parent'], new['name'], new['fsid'], new['size'], new['mtime_ns'],
                 digest, new['preview_state'], new['preview'], new['metadata'], new['sort_time'], new['error'],
                 time.time(), old['id']))
            db.execute('INSERT OR REPLACE INTO preview_cleanup VALUES (?,?)', (old['id'], time.time()+1800))

    def reconcile_pending_identities(self):
        for row in self.db.rows(
                "SELECT DISTINCT hash,size FROM media WHERE availability='online' "
                "AND identity_pending=1 AND hash IS NOT NULL"):
            self.reconcile_identity(row['hash'], row['size'])

    def cleanup_previews(self):
        cache = (self.db.directory / 'previews').resolve()
        for row in self.db.rows('SELECT * FROM preview_cleanup WHERE due<=?',(time.time(),)):
            try:
                for path in cache.glob(row['media_id']+'-*.jpg'):
                    if path.resolve().parent == cache:
                        path.unlink(missing_ok=True)
                self.db.execute('DELETE FROM preview_cleanup WHERE media_id=?',(row['media_id'],))
            except OSError:
                pass

    def scan(self):
        with self.lock:
            if self.interrupt_scan.is_set():
                return
            roots = self.db.enabled_roots()
            inventories = []
            def discover(root, path):
                key = normalized(path)
                current = self.db.one('SELECT * FROM media WHERE path=?', (key,))
                stat = path.stat()
                if not current:
                    fsid = f'{stat.st_dev}:{stat.st_ino}' if stat.st_ino else None
                    moved = self.db.one(
                        "SELECT * FROM media WHERE fsid=? AND path<>?", (fsid, key)) if fsid else None
                    if moved and Path(moved['path']).exists():
                        moved = None
                    if moved:
                        self.db.execute(
                            "UPDATE media SET root_id=?,path=?,parent=?,name=?,size=?,mtime_ns=?,availability='online',updated=? WHERE id=?",
                            (root['id'], key, normalized(path.parent), path.name, stat.st_size, stat.st_mtime_ns,
                             time.time(), moved['id']))
                        current = self.db.one('SELECT * FROM media WHERE id=?', (moved['id'],))
                    else:
                        same_size = self.db.rows(
                            'SELECT path FROM media WHERE size=? AND hash IS NOT NULL LIMIT 32', (stat.st_size,))
                        identity_pending = int(any(not Path(row['path']).exists() for row in same_size))
                        self.db.execute('INSERT INTO media(id,root_id,path,parent,name,kind,fsid,sort_time,size,mtime_ns,identity_pending,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                            (str(uuid.uuid4()),root['id'],key,normalized(path.parent),path.name,
                             'image' if path.suffix.lower() in IMAGES else 'video',fsid,stat.st_mtime,
                             stat.st_size,stat.st_mtime_ns,identity_pending,time.time()))
                        current = self.db.one('SELECT * FROM media WHERE path=?', (key,))
                self.visit(root['id'], path, current, stat)
                self.preview_wake.set()
                self.hash_wake.set()
            # Inventory every accessible root before matching moves. A one-to-many
            # hash match must not depend on directory enumeration order.
            for root in roots:
                inventory = self.inventory(root, lambda path: discover(root, path))
                if inventory is not None:
                    inventories.append((root, inventory))
            for root, inventory in inventories:
                if self.stop.is_set() or self.interrupt_scan.is_set():
                    return
                self.reconcile(root, inventory, set())
            self.reconcile_pending_identities()
            self.preview_wake.set()
            self.hash_wake.set()

    def inventory(self, root, on_discover=None):
        self.db.execute("UPDATE roots SET state='scanning',error=NULL WHERE id=?", (root['id'],))
        paths = []
        # Nested roots keep their own background and ownership. The ancestor
        # scans only the remaining tree, so each file is inventoried once.
        nested = {normalized(Path(r['path'])) for r in self.db.rows('SELECT id,path FROM roots')
                  if r['id'] != root['id'] and Path(root['path']) in Path(r['path']).parents}
        nested.update(r['path'] for r in self.db.rows('SELECT path FROM excluded_paths'))
        try:
            if not Path(root['path']).is_dir():
                raise OSError('目录不可访问')
            def fail(error):
                raise error
            for folder, dirs, files in os.walk(root['path'], onerror=fail, followlinks=False):
                dirs[:] = [d for d in dirs if not Path(folder, d).is_symlink()
                           and normalized(Path(folder, d)) not in nested]
                for name in files:
                    if self.stop.is_set() or self.interrupt_scan.is_set():
                        return None
                    path = Path(folder, name)
                    if path.suffix.lower() not in IMAGES | VIDEOS or path.is_symlink():
                        continue
                    path.stat()
                    paths.append(path)
                    if on_discover:
                        on_discover(path)
            return paths
        except OSError as exc:
            self.db.execute("UPDATE roots SET state='offline',error=? WHERE id=?", (str(exc), root['id']))
            return None

    def scan_root(self, root):
        inventory = self.inventory(root)
        if inventory is not None:
            self.reconcile(root, inventory)

    def reconcile(self, root, inventory, deferred=None):
        seen = {normalized(path) for path in inventory}
        try:
            for path in inventory:
                if self.stop.is_set() or self.interrupt_scan.is_set():
                    return
                if deferred is None or normalized(path) in deferred:
                    current = self.db.one('SELECT * FROM media WHERE path=?', (normalized(path),))
                    self.visit(root['id'], path, current)
            with self.db.connect() as db:
                managed_roots = db.execute('SELECT id,path FROM roots').fetchall()
                for media in db.execute('SELECT id,path FROM media WHERE root_id=?', (root['id'],)).fetchall():
                    if media['path'] not in seen:
                        owners = [r for r in managed_roots
                                  if Path(r['path']) in Path(media['path']).parents]
                        owner = max(owners, key=lambda r: len(r['path'])) if owners else None
                        if owner and owner['id'] != root['id']:
                            db.execute('UPDATE media SET root_id=? WHERE id=?', (owner['id'], media['id']))
                            continue
                        db.execute("UPDATE media SET availability='missing' WHERE id=?", (media['id'],))
                db.execute("UPDATE roots SET state='online',scanned=?,error=NULL WHERE id=?", (time.time(), root['id']))
        except OSError as exc:
            self.db.execute("UPDATE roots SET state='offline',error=? WHERE id=?", (str(exc), root['id']))

    def visit(self, root_id, path, current=None, stat=None):
        key = normalized(path)
        stat = stat or path.stat()
        current = current or self.db.one('SELECT * FROM media WHERE path=?', (key,))
        fsid = f'{stat.st_dev}:{stat.st_ino}' if stat.st_ino else None
        # Newly written files are revisited; do not decode an in-progress copy.
        if time.time() - stat.st_mtime < 2:
            self.wake.set()
            return
        if current and (current['size'], current['mtime_ns']) == (stat.st_size, stat.st_mtime_ns):
            self.db.execute("UPDATE media SET availability='online',root_id=?,fsid=?,size=?,mtime_ns=? WHERE id=?", (root_id, fsid, stat.st_size, stat.st_mtime_ns, current['id']))
            return
        media_id = current['id'] if current else str(uuid.uuid4())
        with self.db.connect() as db:
            if not current:
                db.execute('INSERT INTO media(id,root_id,path,parent,name,kind,sort_time,updated) VALUES (?,?,?,?,?,?,?,?)',
                           (media_id, root_id, key, normalized(path.parent), path.name,
                            'image' if path.suffix.lower() in IMAGES else 'video', stat.st_mtime, time.time()))
            changed = bool(current)
            db.execute("UPDATE media SET root_id=?,path=?,parent=?,name=?,fsid=?,size=?,mtime_ns=?,hash=NULL,availability='online',updated=? WHERE id=?",
                       (root_id, key, normalized(path.parent), path.name, fsid, stat.st_size, stat.st_mtime_ns, time.time(), media_id))
            if changed:
                db.execute("UPDATE media SET preview_state='pending',analysis_state='stale',vector_state='stale',revision=revision+1 WHERE id=?", (media_id,))
                db.execute('DELETE FROM vectors WHERE media_id=?', (media_id,))
                db.execute('DELETE FROM vector_jobs WHERE media_id=?', (media_id,))

    def preview(self, row):
        fingerprint = f"{row['size'] or 0:x}-{row['mtime_ns'] or 0:x}"
        destination = self.db.directory / 'previews' / (row['id'] + '-' + fingerprint + '.jpg')
        try:
            decoder = self.db.settings().get('video_decoder', 'cpu') if row['kind'] == 'video' else 'cpu'
            meta = make_preview(row['path'], destination, row['kind'], decoder)
            updated = self.db.execute("UPDATE media SET preview=?,preview_state='ready',metadata=?,sort_time=?,error=NULL WHERE id=? AND size=? AND mtime_ns=?",
                            (str(destination), json.dumps(meta, ensure_ascii=False),
                             sort_timestamp(meta, row['mtime_ns'] / 1e9), row['id'], row['size'], row['mtime_ns']))
            if not updated:
                destination.unlink(missing_ok=True)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            if isinstance(exc, subprocess.CalledProcessError):
                reason = 'FFmpeg 无法解码，文件可能损坏或编码不受支持'
            self.db.execute("UPDATE media SET preview_state='failed',error=? WHERE id=? AND size=? AND mtime_ns=?",
                            ('预览失败：' + reason, row['id'], row['size'], row['mtime_ns']))
