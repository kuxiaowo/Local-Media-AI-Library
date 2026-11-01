"""Bounded image/video lanes with short-lived, per-browser viewport priorities."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time


class PreviewScheduler:
    def __init__(self, scanner):
        self.scanner = scanner
        self.db = scanner.db
        self.lock = threading.Lock()
        self.clients = {}
        self.running = {'image': 0, 'video': 0}
        self.bursts = {'image': 0, 'video': 0}

    def request(self, client, ids, detail=None):
        with self.lock:
            self.clients.pop(client, None)
            self.clients[client] = (time.monotonic() + 10, list(dict.fromkeys(ids)), detail)
            while len(self.clients) > 32:
                self.clients.pop(next(iter(self.clients)))
        self.scanner.preview_wake.set()

    def priorities(self):
        with self.lock:
            now = time.monotonic()
            self.clients = {k: v for k, v in self.clients.items() if v[0] > now}
            details, visible = [], []
            for _, ids, detail in reversed(list(self.clients.values())):
                if detail:
                    details.append(detail)
                visible.extend(ids)
            return list(dict.fromkeys(details + visible))[:400], set(details)

    def choose(self, kind, active_ids=()):
        root_ids = [r['id'] for r in self.db.enabled_roots()]
        if not root_ids:
            return None
        ids, details = self.priorities()
        base = ("availability='online' AND preview_state='pending' AND kind=? "
                "AND size IS NOT NULL AND mtime_ns<? AND root_id IN (" + ','.join('?' for _ in root_ids) + ")")
        args = [kind, time.time_ns() - 2_000_000_000, *root_ids]
        def available(rows):
            return [r for r in rows if r['id'] not in active_ids]
        foreground = available(self.db.rows('SELECT * FROM media WHERE ' + base +
                              ' AND id IN (' + ','.join('?' for _ in ids) + ')', [*args, *ids])) if ids else []
        def stage(row):
            return 'thumbnail' if kind == 'image' and row['thumbnail_state'] == 'pending' and row['id'] not in details else 'standard'
        def rank(row):
            return (0 if row['id'] in details else 1 if stage(row) == 'thumbnail' else 2, ids.index(row['id']))
        foreground.sort(key=rank)
        normal = []
        if not foreground or self.bursts[kind] >= 8:
            normal_base, normal_args = base, args
            if foreground:
                normal_base += ' AND id NOT IN (' + ','.join('?' for _ in ids) + ')'
                normal_args = [*args, *ids]
            if kind == 'image':
                normal = available(self.db.rows(
                    'SELECT * FROM media INDEXED BY media_thumbnail_queue WHERE ' + normal_base +
                    " AND thumbnail_state='pending' ORDER BY updated,id LIMIT 32", normal_args))
            if not normal:
                normal = available(self.db.rows(
                    'SELECT * FROM media INDEXED BY media_preview_queue WHERE ' + normal_base +
                    (" AND thumbnail_state<>'pending'" if kind == 'image' else '') +
                    ' ORDER BY updated,id LIMIT 32', normal_args))
        # Let older background work make progress even while a user keeps scrolling.
        background = next((r for r in normal if r['id'] not in ids), None)
        if foreground and (self.bursts[kind] < 8 or background is None):
            self.bursts[kind] += 1
            row = foreground[0]
        elif normal:
            self.bursts[kind] = 0
            row = background or normal[0]
        else:
            return None
        return row, stage(row)

    def loop(self):
        active = {}
        cleaned = 0
        with ThreadPoolExecutor(max_workers=12, thread_name_prefix='media-preview') as pool:
            while not self.scanner.stop.is_set():
                self.scanner.preview_wake.clear()
                for future in list(active):
                    if future.done():
                        try:
                            future.result()
                        except Exception as exc:
                            self.db.event('预览调度失败：' + type(exc).__name__)
                        del active[future]
                config = self.db.settings()
                for kind, maximum in [('image', 8), ('video', 4)]:
                    count = sum(row['kind'] == kind for row in active.values())
                    limit = min(maximum, max(1, config['preview_' + kind + '_concurrency']))
                    while count < limit and not self.scanner.stop.is_set():
                        with self.scanner.preview_lock:
                            selected = self.choose(kind, {r['id'] for r in active.values()})
                            if not selected:
                                break
                            row, tier = selected
                            future = pool.submit(self.scanner.preview, row, tier)
                            active[future] = row
                            future.add_done_callback(lambda _: self.scanner.preview_wake.set())
                        count += 1
                self.running = {kind: sum(r['kind'] == kind for r in active.values()) for kind in ('image', 'video')}
                if active:
                    self.scanner.preview_active.set()
                else:
                    self.scanner.preview_active.clear()
                if time.monotonic() - cleaned >= 30:
                    self.scanner.cleanup_previews()
                    cleaned = time.monotonic()
                self.scanner.preview_wake.wait(.5 if active else 5)
        self.scanner.preview_active.clear()
        self.running = {'image': 0, 'video': 0}
