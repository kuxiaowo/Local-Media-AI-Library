"""SQLite is authoritative; immutable, normalized matrix chunks are disposable caches."""
from __future__ import annotations

import json
import threading
import numpy as np

from .db import profile


def normalize(vector):
    value = np.asarray(vector, dtype=np.float32)
    if value.ndim != 1 or not len(value) or not np.isfinite(value).all():
        raise ValueError('向量必须为有限的一维数组')
    norm = np.linalg.norm(value)
    if norm <= 0:
        raise ValueError('向量不能为零')
    return value / norm


def enqueue(conn, media_id, revision, config):
    public = {k: v for k, v in config.items() if k not in {'api_key', 'embedding_key'}}
    conn.execute("INSERT OR REPLACE INTO vector_jobs(media_id,profile,revision,config) VALUES (?,?,?,?)",
                 (media_id, profile(config), revision, json.dumps(public)))


def enqueue_rebuilds(conn, media_id, revision):
    for rebuild in conn.execute("SELECT config FROM rebuilds WHERE state='running'").fetchall():
        enqueue(conn, media_id, revision, json.loads(rebuild['config']))


class VectorIndex:
    def __init__(self, db):
        self.db = db
        self.lock = threading.Lock()
        self.cache = {}

    def chunks(self, space):
        version = self.db.one('SELECT version FROM index_version WHERE id=1')['version']
        with self.lock:
            if space in self.cache and self.cache[space][0] == version:
                return self.cache[space][1]
            chunks = []
            with self.db.connect() as conn:
                cursor = conn.execute('SELECT media_id,revision,dim,data FROM vectors WHERE profile=? ORDER BY dim,media_id', (space,))
                pending = []
                dimension = None
                def flush():
                    if pending:
                        chunks.append((np.array([r['media_id'] for r in pending]),
                                       np.array([r['revision'] for r in pending]),
                                       np.stack([np.frombuffer(r['data'], dtype=np.float32) for r in pending])))
                for row in cursor:
                    if pending and (len(pending) >= 4096 or row['dim'] != dimension):
                        flush()
                        pending = []
                    dimension = row['dim']
                    pending.append(row)
                flush()
            self.cache = {space: (version, chunks)}
            return chunks

    def search(self, query, space, where='m.availability=\'online\'', args=(), limit=100):
        query = normalize(query)
        # One filtered metadata query, never per-vector SQLite/Python cosine calls.
        valid = {r['id']: r['revision'] for r in self.db.rows('SELECT m.id,m.revision FROM media m WHERE ' + where, args)}
        best = []
        for ids, revisions, matrix in self.chunks(space):
            if matrix.shape[1] != len(query):
                continue
            mask = np.fromiter((valid.get(i) == int(r) for i, r in zip(ids, revisions)), bool, len(ids))
            positions = np.flatnonzero(mask)
            if not len(positions):
                continue
            scores = matrix[positions] @ query
            count = min(limit, len(scores))
            top = np.argpartition(scores, -count)[-count:]
            best.extend((str(ids[positions[i]]), float(scores[i])) for i in top)
        return sorted(best, key=lambda x: x[1], reverse=True)[:limit]
