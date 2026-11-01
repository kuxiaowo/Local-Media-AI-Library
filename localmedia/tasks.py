"""Durable analysis control. A checkpoint is published only after a complete stage."""
from __future__ import annotations

import json
import hashlib
import time
import uuid
from pathlib import Path

from .frame_cache import complete as frame_cache_complete


class Paused(Exception):
    pass


class Tasks:
    def __init__(self, db):
        self.db = db

    def visual_references(self):
        directory = self.db.directory / 'visual-reference-pack'
        if not directory.is_dir():
            return []
        result = []
        for path in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            if not path.is_file() or path.suffix.lower() not in {'.png', '.jpg', '.jpeg'}:
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            result.append({'category': path.stem, 'path': str(path.resolve()), 'sha256': digest})
        return result

    def create(self, media_ids, force=False, run_kind='analysis'):
        if run_kind not in {'analysis', 'frames'}:
            raise ValueError('未知任务类型')
        run_id = str(uuid.uuid4())
        config = self.db.settings()
        if config.get('active_profile'):
            provider, url, model = json.loads(config['active_profile'])
            config.update(embedding_provider=provider, embedding_url=url, embedding_model=model)
        # Credentials are resolved only at execution time, never copied into jobs.
        config = {k: v for k, v in config.items() if k not in {'api_key', 'embedding_key'}}
        now = time.time()
        reference_snapshot = None
        with self.db.connect() as conn:
            backgrounds = sorted(conn.execute('SELECT path,background FROM roots').fetchall(),
                                 key=lambda r: len(Path(r['path']).parts))
            chains = {}
            conn.execute('INSERT INTO runs(id,state,config,created,kind) VALUES (?,?,?,?,?)',
                         (run_id, 'running', json.dumps(config), now, run_kind))
            for media_id in dict.fromkeys(media_ids):
                row = conn.execute(
                    'SELECT m.*,r.background,r.visual_enhancement FROM media m '
                    'JOIN roots r ON r.id=m.root_id WHERE m.id=?',
                    (media_id,),
                ).fetchone()
                if not row or row['availability'] != 'online':
                    continue
                if run_kind == 'frames' and row['kind'] != 'video':
                    continue
                if run_kind == 'frames' and frame_cache_complete(self.db, dict(row), config):
                    continue
                if run_kind == 'analysis' and not force and row['analysis_state'] == 'done':
                    continue
                active = conn.execute("SELECT 1 FROM tasks WHERE media_id=? AND state IN ('queued','running','paused')", (media_id,)).fetchone()
                if active:
                    continue
                snapshot = {
                    **config,
                    'task_kind': run_kind,
                    'background': row['background'],
                    'note': row['note'],
                    'original_filename': row['name'],
                    'source_directory': row['parent'],
                }
                if run_kind == 'analysis' and row['visual_enhancement']:
                    if reference_snapshot is None:
                        reference_snapshot = self.visual_references()
                    if reference_snapshot:
                        snapshot['visual_references'] = reference_snapshot
                        snapshot['visual_reference_count'] = len(reference_snapshot)
                if row['parent'] not in chains:
                    directory = Path(row['parent'])
                    chains[row['parent']] = [
                        {'directory': r['path'], 'background': r['background']}
                        for r in backgrounds if r['background'].strip()
                        and (Path(r['path']) == directory or Path(r['path']) in directory.parents)]
                snapshot['background_chain'] = chains[row['parent']]
                snapshot['source_revision'] = row['revision']
                conn.execute('INSERT INTO tasks(id,run_id,media_id,source_hash,config,updated) VALUES (?,?,?,?,?,?)',
                             (str(uuid.uuid4()), run_id, media_id, row['hash'], json.dumps(snapshot, ensure_ascii=False), now))
            if not conn.execute('SELECT 1 FROM tasks WHERE run_id=?', (run_id,)).fetchone():
                conn.execute("UPDATE runs SET state='completed' WHERE id=?", (run_id,))
        return run_id

    def pause(self, run_id, reason='manual', immediate=False):
        with self.db.connect() as conn:
            conn.execute("UPDATE runs SET state='pausing',reason=? WHERE id=? AND state='running'", (reason, run_id))
            conn.execute("UPDATE tasks SET state='paused',reason=? WHERE run_id=? AND state='queued'", (reason, run_id))
            if immediate:
                conn.execute("UPDATE tasks SET reason='interrupt' WHERE run_id=? AND state='running'", (run_id,))
            if not conn.execute("SELECT 1 FROM tasks WHERE run_id=? AND state='running'", (run_id,)).fetchone():
                conn.execute("UPDATE runs SET state='paused' WHERE id=? AND state='pausing'", (run_id,))

    def resume(self, run_id):
        budget = self.db.settings()['video_budget']
        with self.db.connect() as conn:
            for task in conn.execute("SELECT t.*,m.hash,m.kind,m.availability FROM tasks t JOIN media m ON m.id=t.media_id WHERE run_id=? AND t.state='paused'", (run_id,)).fetchall():
                newer = conn.execute("SELECT 1 FROM tasks WHERE media_id=? AND id<>? AND (state IN ('queued','running') OR (state='completed' AND updated>?)) LIMIT 1", (task['media_id'],task['id'],task['updated'])).fetchone()
                if newer:
                    conn.execute("UPDATE tasks SET state='failed',error='已有更新的分析任务，旧任务不能覆盖新结果' WHERE id=?", (task['id'],))
                    continue
                config = json.loads(task['config'])
                if not (config.get('video_frame_mode', 'quality') in {'quality', 'fast'}
                        and config.get('frame_interval',0)>0 and config.get('scene_fps',3)>0
                        and config.get('batch_size',0)>config.get('batch_overlap',0)>=0):
                    conn.execute("UPDATE tasks SET state='failed',error='任务配置无效，请重新创建' WHERE id=?", (task['id'],))
                    continue
                if task['availability'] != 'online':
                    continue
                if task['source_hash'] != task['hash']:
                    conn.execute("UPDATE tasks SET state='failed',error='媒体内容改变，请重新分析' WHERE id=?", (task['id'],))
                    continue
                if task['reason'] == 'source_changed':
                    conn.execute("UPDATE tasks SET state='failed',error='媒体内容改变，请重新分析' WHERE id=?", (task['id'],))
                    continue
                if task['kind'] == 'video' and budget and task['elapsed'] >= budget:
                    continue
                conn.execute("UPDATE tasks SET state='queued',reason=NULL,error=NULL WHERE id=?", (task['id'],))
            if conn.execute("SELECT 1 FROM tasks WHERE run_id=? AND state='queued'", (run_id,)).fetchone():
                conn.execute("UPDATE runs SET state='running',reason=NULL WHERE id=?", (run_id,))

    def queued_kinds(self):
        rows = self.db.rows(
            "SELECT DISTINCT m.kind FROM tasks t "
            "JOIN runs r ON r.id=t.run_id JOIN media m ON m.id=t.media_id "
            "WHERE t.state='queued' AND r.state='running'"
        )
        return {row['kind'] for row in rows}

    def claim(self, kind=None):
        if kind not in {None, 'image', 'video'}:
            raise ValueError('未知媒体类型')
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            sql = ("SELECT t.*,m.kind AS media_kind,r.kind AS run_kind FROM tasks t JOIN runs r ON r.id=t.run_id JOIN media m ON m.id=t.media_id "
                   "WHERE t.state='queued' AND r.state='running' AND m.hash IS NOT NULL "
                   "AND t.source_hash IS NOT NULL ")
            args = ()
            if kind is not None:
                sql += "AND m.kind=? "
                args = (kind,)
            # rowid is the immutable insertion order. Checkpoints and retries
            # update timestamps, but must never reshuffle the visible queue.
            row = conn.execute(sql + "ORDER BY t.rowid LIMIT 1", args).fetchone()
            if not row:
                return None
            conn.execute("UPDATE tasks SET state='running',error=NULL,updated=? WHERE id=?", (time.time(), row['id']))
            return dict(row)

    def checkpoint(self, task_id, checkpoint, elapsed):
        with self.db.connect() as conn:
            conn.execute('UPDATE tasks SET checkpoint=?,elapsed=elapsed+?,updated=? WHERE id=?',
                         (json.dumps(checkpoint, ensure_ascii=False), max(0, elapsed), time.time(), task_id))

    def boundary(self, task_id):
        row = self.db.one('SELECT t.*,r.state AS run_state FROM tasks t JOIN runs r ON r.id=t.run_id WHERE t.id=?', (task_id,))
        if row['run_state'] != 'running':
            self.db.execute("UPDATE tasks SET state='paused',reason=COALESCE(reason,'manual') WHERE id=?", (task_id,))
            self.db.execute("UPDATE runs SET state='paused' WHERE id=? AND state='pausing'", (row['run_id'],))
            raise Paused()
        media = self.db.one('SELECT * FROM media WHERE id=?', (row['media_id'],))
        budget = self.db.settings()['video_budget']
        if media['kind'] == 'video' and budget and row['elapsed'] >= budget:
            self.db.execute("UPDATE tasks SET state='paused',reason='time_budget' WHERE id=?", (task_id,))
            raise Paused()
        if media['hash'] != row['source_hash'] or media['availability'] != 'online':
            self.db.execute("UPDATE tasks SET state='paused',reason='source_changed' WHERE id=?", (task_id,))
            raise Paused()
        return row
