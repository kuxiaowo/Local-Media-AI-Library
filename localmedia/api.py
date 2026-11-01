from __future__ import annotations

import asyncio
import contextlib
import json
import math
import multiprocessing
import re
import os
import time
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .db import Database, DEFAULTS, profile
from .index import VectorIndex, enqueue, enqueue_rebuilds
from .instance import library_lock
from .media import normalized
from .scanner import Scanner
from .tasks import Tasks
from .worker import Worker
from .worker_service import WorkerClient, run_worker
from .folder_picker import choose_folder
from .playback import PlaybackCache, PlaybackFileResponseMixin


class MediaFileResponse(PlaybackFileResponseMixin, FileResponse):
    pass


class RootInput(BaseModel):
    path: str = Field(min_length=1)
    background: str = ''
    visual_enhancement: bool = False


class TextInput(BaseModel):
    text: str = Field(max_length=100000)


class EnabledInput(BaseModel):
    enabled: bool


class RunInput(BaseModel):
    ids: list[str] | None = None
    root_id: str | None = None
    parent: str | None = None
    force: bool = False
    cloud_ack: bool = False
    operation: Literal['analysis', 'frames'] = 'analysis'


# The task log is authoritative, including failures from older app versions.
# Ignore failures superseded by a newer task or by a manual description edit.
ANALYSIS_ERROR_SQL = """(
    SELECT CASE WHEN t.state='failed'
        AND (json_extract(t.config,'$.source_revision') IS NULL
             OR json_extract(t.config,'$.source_revision')=m.revision)
        THEN COALESCE(NULLIF(t.error,''),'分析失败，请重试') END
    FROM tasks t JOIN runs r ON r.id=t.run_id
    WHERE t.media_id=m.id AND r.kind='analysis'
    ORDER BY t.rowid DESC LIMIT 1
)"""


def analysis_status(row):
    if row.get('analysis_error'):
        row['analysis_state'] = 'failed'
    return row


def display_title(description):
    lines = [line.strip() for line in (description or '').splitlines() if line.strip()]
    if not lines:
        return ''
    first = lines[0].replace('**', '').strip('# ').strip()
    title = re.sub(r'^(?:标题|Title)\s*[:：]\s*', '', first, flags=re.I)
    if title in {'标题', 'Title'} and len(lines)>1:
        title=lines[1].strip('*# ')
    return title.strip('“”"') if len(title)<=80 else ''


def filters(root_id=None, parent=None, kind=None, status=None, missing=False, after=None, before=None, q=None):
    where = [
        'm.availability=?',
        """NOT EXISTS (
            SELECT 1 FROM roots owner
            JOIN roots disabled ON disabled.enabled=0
              AND (owner.id=disabled.id OR substr(owner.path,1,length(rtrim(disabled.path,'/\\'))+1)=rtrim(disabled.path,'/\\') || ?)
            WHERE owner.id=m.root_id
        )""",
    ]
    args = ['missing' if missing else 'online', os.sep]
    if root_id:
        where.append("m.root_id IN (SELECT child.id FROM roots child JOIN roots ancestor ON ancestor.id=? WHERE child.id=ancestor.id OR substr(child.path,1,length(rtrim(ancestor.path,'/\\'))+1)=rtrim(ancestor.path,'/\\') || ?)")
        args.extend([root_id, os.sep])
    if kind:
        where.append('m.kind=?')
        args.append(kind)
    if status == 'failed':
        where.append(ANALYSIS_ERROR_SQL + ' IS NOT NULL')
    elif status:
        where.extend(['m.analysis_state=?', ANALYSIS_ERROR_SQL + ' IS NULL'])
        args.append(status)
    if parent:
        prefix = parent.rstrip('/\\') + os.sep
        escaped = prefix.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        where.append("(m.parent=? OR m.parent LIKE ? ESCAPE '\\')")
        args.extend([parent, escaped+'%'])
    if after is not None:
        where.append('m.sort_time>=?')
        args.append(after)
    if before is not None:
        where.append('m.sort_time<?')
        args.append(before)
    if q:
        where.append("(m.name LIKE ? ESCAPE '\\' OR m.description LIKE ? ESCAPE '\\')")
        escaped = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        args += ['%' + escaped + '%'] * 2
    return ' AND '.join(where), args


def create_app(directory=None, background=True, allowed_hosts=None, separate_worker=False):
    db = Database(Path(directory or os.environ.get('LOCALMEDIA_DATA', 'data/localmedia-v2')))
    tasks, scanner, index = Tasks(db), Scanner(db), VectorIndex(db)
    worker = Worker(db, tasks)
    playback = PlaybackCache(db.directory)
    worker_client = WorkerClient(db) if separate_worker else None

    async def cancel_work(*, media_ids=None, run_id=None):
        if worker_client:
            try:
                await worker_client.cancel(media_ids=media_ids, run_id=run_id)
            except RuntimeError as exc:
                raise HTTPException(503, str(exc)) from None
        else:
            await worker.cancel(media_ids=media_ids, run_id=run_id)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        with library_lock(db.directory):
            process = stop_event = runner = monitor = None
            if not separate_worker:
                db.restart_recovery()
            if background:
                if separate_worker:
                    context = multiprocessing.get_context('spawn')
                    stop_event = context.Event()
                    process = context.Process(target=run_worker, args=(db.directory, stop_event),
                                              name='localmedia-ai-worker')
                    process.start()
                    app.state.worker_process = process
                    async def monitor_worker():
                        nonlocal process
                        while not stop_event.is_set():
                            await asyncio.sleep(2)
                            if not stop_event.is_set() and not process.is_alive():
                                await asyncio.to_thread(process.join, 0)
                                process = context.Process(target=run_worker, args=(db.directory, stop_event),
                                                          name='localmedia-ai-worker')
                                process.start()
                                app.state.worker_process = process
                    monitor = asyncio.create_task(monitor_worker())
                else:
                    runner = asyncio.create_task(worker.loop())
                scanner.start()
            try:
                yield
            finally:
                await asyncio.to_thread(playback.close)
                if background:
                    if monitor:
                        stop_event.set()
                        monitor.cancel()
                        await asyncio.gather(monitor, return_exceptions=True)
                    await worker.close()
                    await asyncio.to_thread(scanner.close)
                    if runner:
                        runner.cancel()
                        await asyncio.gather(runner, return_exceptions=True)
                    if process:
                        stop_event.set()
                        await asyncio.to_thread(process.join, 10)
                        if process.is_alive():
                            process.terminate()
                            await asyncio.to_thread(process.join, 5)

    app = FastAPI(title='LocalMedia', lifespan=lifespan)
    app.state.db, app.state.worker = db, worker
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=allowed_hosts or ['localhost', '127.0.0.1', '[::1]', 'testserver'],
    )

    @app.middleware('http')
    async def local_origin(request: Request, call_next):
        origin = request.headers.get('origin')
        if request.method not in {'GET', 'HEAD', 'OPTIONS'} and origin:
            if urlparse(origin).netloc != request.headers.get('host'):
                return JSONResponse({'detail': '只允许本机同源操作'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    def require_media(ident):
        row = db.one('SELECT * FROM media WHERE id=?', (ident,))
        if not row:
            raise HTTPException(404, '媒体不存在')
        return row

    def root_path(path, excluding=None):
        result = Path(path).resolve()
        if not result.is_dir():
            raise HTTPException(400, '目录不存在或不可访问')
        data = db.directory
        if result == data or result in data.parents or data in result.parents:
            raise HTTPException(400, '媒体目录不能包含图库缓存，也不能位于缓存中')
        for row in db.rows('SELECT id,path FROM roots'):
            if row['id'] == excluding:
                continue
            other = Path(row['path'])
            if result == other:
                raise HTTPException(400, '该目录已经添加')
        return normalized(result)

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'version': 2}

    picker_lock = asyncio.Lock()

    @app.post('/api/directories/pick')
    async def pick_directory(body: TextInput):
        if picker_lock.locked():
            raise HTTPException(409, '目录选择框已打开，请先完成选择')
        async with picker_lock:
            try:
                return {'path': await choose_folder(body.text)}
            except RuntimeError as exc:
                raise HTTPException(503, str(exc)) from None

    @app.get('/api/roots')
    def roots():
        rows = db.rows('SELECT r.*,(SELECT COUNT(*) FROM media m WHERE m.root_id=r.id AND m.availability=\'online\') AS count FROM roots r')
        enabled_ids = {row['id'] for row in db.enabled_roots()}
        for row in rows:
            row['enabled'] = bool(row['enabled'])
            row['visual_enhancement'] = bool(row['visual_enhancement'])
            row['effective_enabled'] = row['id'] in enabled_ids
        for row in rows:
            ancestors = [candidate for candidate in rows
                         if Path(candidate['path']) in Path(row['path']).parents]
            nearest = max(ancestors, key=lambda candidate: len(Path(candidate['path']).parts)) if ancestors else None
            row['parent_id'] = nearest['id'] if nearest else None
            row['total_count'] = sum(child['count'] for child in rows
                                     if child['effective_enabled'] and
                                     (child['id'] == row['id'] or Path(row['path']) in Path(child['path']).parents))
        return rows

    @app.get('/api/folder-tree')
    def folder_tree():
        managed = db.enabled_roots()
        nodes = {}
        def ensure(path, owner):
            key = normalized(path)
            if key not in nodes:
                nodes[key] = {'path':key, 'root_id':owner['id'], 'parent_path':None, 'count':0}
            return nodes[key]
        for owner in managed:
            ensure(Path(owner['path']), owner)
        for row in db.rows("SELECT root_id,parent,COUNT(*) AS count FROM media WHERE availability='online' GROUP BY root_id,parent"):
            owner = next((r for r in managed if r['id']==row['root_id']), None)
            if not owner:
                continue
            path = Path(row['parent'])
            while path == Path(owner['path']) or Path(owner['path']) in path.parents:
                ensure(path, owner)
                if path == Path(owner['path']):
                    break
                path = path.parent
            ensure(Path(row['parent']), owner)['count'] += row['count']
        # Fill intermediate directories between separately registered roots too.
        for owner in managed:
            ancestors = [r for r in managed if Path(r['path']) in Path(owner['path']).parents]
            if ancestors:
                ancestor = max(ancestors, key=lambda r:len(Path(r['path']).parts))
                path = Path(owner['path']).parent
                while path != Path(ancestor['path']):
                    ensure(path, ancestor)
                    path = path.parent
        for node in nodes.values():
            parent = normalized(Path(node['path']).parent)
            if parent in nodes and parent != node['path']:
                node['parent_path'] = parent
        for node in sorted(nodes.values(), key=lambda n:len(Path(n['path']).parts), reverse=True):
            if node['parent_path']:
                nodes[node['parent_path']]['count'] += node['count']
        return list(nodes.values())

    @app.post('/api/roots')
    async def add_root(body: RootInput):
        path = root_path(body.path)
        db.execute('DELETE FROM excluded_paths WHERE path=?',(path,))
        ident = str(uuid.uuid4())
        db.execute('INSERT INTO roots(id,path,background,visual_enhancement) VALUES (?,?,?,?)',
                   (ident, path, body.background, int(body.visual_enhancement)))
        if background:
            await asyncio.to_thread(scanner.watch)
        scanner.wake.set()
        return {'id': ident, 'path': path}

    @app.post('/api/directories/remove')
    async def remove_directory(body: RootInput):
        target = Path(body.path).resolve()
        managed = db.rows('SELECT * FROM roots')
        if not any(target == Path(r['path']) or Path(r['path']) in target.parents for r in managed):
            raise HTTPException(404,'目录不在图库管理范围内')
        affected = {m['id'] for m in db.rows('SELECT id,path FROM media') if target in Path(m['path']).parents}
        scanner.interrupt_scan.set()
        with db.connect() as conn:
            for ident in affected:
                conn.execute("UPDATE tasks SET state='paused',reason='directory_removed' WHERE media_id=? AND state IN ('queued','running')",(ident,))
                conn.execute("UPDATE vector_jobs SET state='paused' WHERE media_id=?",(ident,))
        if affected:
            await cancel_work(media_ids=affected)
        def remove():
            with scanner.lock, scanner.preview_lock:
                managed = db.rows('SELECT * FROM roots')
                if not any(target == Path(r['path']) or Path(r['path']) in target.parents for r in managed):
                    raise HTTPException(404,'目录不在图库管理范围内')
                rows = [m for m in db.rows('SELECT id,path FROM media') if target in Path(m['path']).parents]
                ids = {m['id'] for m in rows}
                due = time.time()+1800
                with db.connect() as conn:
                    for ident in ids:
                        conn.execute('INSERT OR REPLACE INTO preview_cleanup VALUES (?,?)',(ident,due))
                        conn.execute('DELETE FROM tasks WHERE media_id=?',(ident,))
                        conn.execute('DELETE FROM media WHERE id=?',(ident,))
                    for r in managed:
                        if target == Path(r['path']) or target in Path(r['path']).parents:
                            conn.execute('DELETE FROM roots WHERE id=?',(r['id'],))
                    conn.execute('INSERT OR IGNORE INTO excluded_paths VALUES (?)',(normalized(target),))
                return {'removed':len(ids),'cleanup_after':due}
        try:
            result = await asyncio.to_thread(remove)
        finally:
            scanner.interrupt_scan.clear()
            scanner.wake.set()
        if not separate_worker:
            worker.settle()
        if background:
            await asyncio.to_thread(scanner.watch)
        scanner.wake.set()
        return result

    @app.patch('/api/roots/{ident}/background')
    def root_background(ident: str, body: TextInput):
        if not db.execute('UPDATE roots SET background=? WHERE id=?', (body.text, ident)):
            raise HTTPException(404, '目录不存在')
        return {'ok': True}

    @app.patch('/api/roots/{ident}/visual-enhancement')
    def root_visual_enhancement(ident: str, body: EnabledInput):
        if not db.execute('UPDATE roots SET visual_enhancement=? WHERE id=?',
                          (int(body.enabled), ident)):
            raise HTTPException(404, '目录不存在')
        return {'ok': True, 'visual_enhancement': body.enabled}

    @app.patch('/api/roots/{ident}/location')
    async def relocate(ident: str, body: RootInput):
        path = root_path(body.path, ident)
        if not db.execute("UPDATE roots SET path=?,state=CASE WHEN enabled=1 THEN 'pending' ELSE 'disabled' END,error=NULL WHERE id=?", (path, ident)):
            raise HTTPException(404, '目录不存在')
        if background:
            await asyncio.to_thread(scanner.watch)
        scanner.wake.set()
        return {'ok': True, 'path': path}

    @app.patch('/api/roots/{ident}/enabled')
    async def root_enabled(ident: str, body: EnabledInput):
        if not db.one('SELECT id FROM roots WHERE id=?', (ident,)):
            raise HTTPException(404, '目录不存在')
        scanner.interrupt_scan.set()
        try:
            def update():
                with scanner.lock:
                    db.execute(
                        "UPDATE roots SET enabled=?,state=?,error=NULL WHERE id=?",
                        (int(body.enabled), 'pending' if body.enabled else 'disabled', ident),
                    )
            await asyncio.to_thread(update)
        finally:
            scanner.interrupt_scan.clear()
        if background:
            await asyncio.to_thread(scanner.watch)
        scanner.wake.set()
        return {'ok': True, 'enabled': body.enabled}

    @app.post('/api/sync')
    def sync():
        scanner.wake.set()
        return {'ok': True}

    @app.get('/api/folders')
    def folders(root_id: str):
        where, args = filters(root_id=root_id)
        return db.rows('SELECT parent,COUNT(*) AS count FROM media m WHERE '+where+' GROUP BY parent ORDER BY parent', args)

    @app.get('/api/media')
    def media(root_id: str | None = None, parent: str | None = None, kind: str | None = None,
              status: str | None = None, missing: bool = False, after: float | None = None,
              before: float | None = None, q: str | None = None,
              offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
        where, args = filters(root_id, parent, kind, status, missing, after, before, q)
        total = db.one('SELECT COUNT(*) AS n FROM media m WHERE ' + where, args)['n']
        rows = db.rows('SELECT m.id,m.path,m.name,m.description,m.kind,m.sort_time,m.preview_state,m.analysis_state,m.vector_state,m.availability,m.error,' + ANALYSIS_ERROR_SQL + ' AS analysis_error FROM media m WHERE '
                       + where + ' ORDER BY m.sort_time DESC,m.id LIMIT ? OFFSET ?', [*args, limit, offset])
        for row in rows:
            row['title'] = display_title(row.pop('description', ''))
            analysis_status(row)
        return {'total': total, 'items': rows}

    @app.get('/api/media/{ident}')
    def detail(ident: str):
        row = require_media(ident)
        row.update(db.one('SELECT ' + ANALYSIS_ERROR_SQL + ' AS analysis_error FROM media m WHERE m.id=?', (ident,)))
        analysis_status(row)
        row['title'] = display_title(row['description'])
        row['metadata'] = json.loads(row['metadata'])
        batches = db.one("SELECT checkpoint FROM tasks WHERE media_id=? AND state='completed' ORDER BY updated DESC LIMIT 1", (ident,))
        cp = json.loads(batches['checkpoint']) if batches else {}
        row['batches'] = cp.get('batches', [])
        # Keep every extraction generation on disk/in the catalog, but do not
        # mix historical re-analysis runs into one duplicated detail timeline.
        generation = db.one(
            'SELECT task_id,MAX(created) AS latest FROM video_frames WHERE media_id=? '
            'GROUP BY task_id ORDER BY latest DESC LIMIT 1',
            (ident,),
        )
        row['frames'] = db.rows(
            'SELECT id,time,end_time,kind FROM video_frames WHERE media_id=? AND task_id IS ? ORDER BY time,id',
            (ident, generation['task_id'] if generation else None),
        ) if generation else []
        row['frame_generations'] = db.one(
            'SELECT COUNT(DISTINCT task_id) AS count,COUNT(*) AS retained FROM video_frames WHERE media_id=?',
            (ident,),
        )
        return row

    @app.get('/api/media/{ident}/frames/{number}')
    def representative(ident: str, number: int):
        require_media(ident)
        frame = db.one('SELECT path FROM video_frames WHERE id=? AND media_id=?', (number, ident))
        if not frame:
            raise HTTPException(404, '抽帧不存在')
        path = Path(frame['path']).resolve()
        if db.directory not in path.parents or not path.is_file():
            raise HTTPException(404, '抽帧缓存不可用')
        return FileResponse(path, media_type='image/jpeg')

    @app.get('/api/media/{ident}/preview')
    def preview(ident: str):
        row = require_media(ident)
        if not row['preview'] or not Path(row['preview']).is_file():
            raise HTTPException(404, row['error'] or '预览尚未生成')
        return FileResponse(row['preview'], media_type='image/jpeg')

    @app.get('/api/media/{ident}/original')
    def original(ident: str):
        row = require_media(ident)
        if row['availability'] != 'online' or not Path(row['path']).is_file():
            raise HTTPException(404, '原文件当前不可访问')
        return MediaFileResponse(row['path'])

    @app.post('/api/media/{ident}/playback')
    def prepare_playback(ident: str):
        row = require_media(ident)
        if row['kind'] != 'video' or row['availability'] != 'online' or not Path(row['path']).is_file():
            raise HTTPException(404, '视频当前不可访问')
        return playback.request(row['path'])

    @app.get('/api/media/{ident}/playback')
    def playback_file(ident: str):
        row = require_media(ident)
        if row['kind'] != 'video' or row['availability'] != 'online' or not Path(row['path']).is_file():
            raise HTTPException(404, '视频当前不可访问')
        target = playback.target(row['path'])
        if not target.is_file():
            raise HTTPException(404, '播放副本尚未就绪')
        return MediaFileResponse(target, media_type='video/mp4')

    @app.patch('/api/media/{ident}/note')
    def note(ident: str, body: TextInput):
        require_media(ident)
        db.execute('UPDATE media SET note=? WHERE id=?', (body.text, ident))
        return {'ok': True}

    @app.post('/api/previews/retry')
    def retry_previews():
        db.execute("UPDATE media SET preview_state='pending',error=NULL WHERE preview_state='failed' AND availability='online'")
        scanner.preview_wake.set()
        return {'ok': True}

    @app.patch('/api/media/{ident}/description')
    def description(ident: str, body: TextInput):
        require_media(ident)
        config = db.settings()
        with db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            active = json.loads(conn.execute("SELECT value FROM settings WHERE key='active_profile'").fetchone()['value'])
            if active:
                provider, url, model = json.loads(active)
                config.update(embedding_provider=provider, embedding_url=url, embedding_model=model)
            conn.execute("UPDATE media SET description=?,revision=revision+1,analysis_state='done',vector_state='pending' WHERE id=?", (body.text, ident))
            conn.execute('DELETE FROM vectors WHERE media_id=?', (ident,))
            revision = conn.execute('SELECT revision FROM media WHERE id=?', (ident,)).fetchone()['revision']
            enqueue(conn, ident, revision, config)
            enqueue_rebuilds(conn, ident, revision)
        return {'ok': True}

    @app.delete('/api/media/{ident}/missing-record')
    def clean(ident: str):
        row = require_media(ident)
        if row['availability'] != 'missing':
            raise HTTPException(409, '只能清理失联记录')
        with db.connect() as conn:
            conn.execute('DELETE FROM tasks WHERE media_id=?', (ident,))
            conn.execute('DELETE FROM media WHERE id=?', (ident,))
        return {'ok': True}

    @app.post('/api/runs')
    def create_run(body: RunInput):
        config = db.settings()
        active_embedding_provider = json.loads(config['active_profile'])[0] if config['active_profile'] else config['embedding_provider']
        cloud = config['provider'] in {'codex', 'openai'} or active_embedding_provider == 'openai'
        if body.operation == 'analysis' and cloud and not body.cloud_ack:
            raise HTTPException(400, '请确认云端会接收预览、视频抽帧及背景文字')
        ids = body.ids
        if ids is None:
            where, args = filters(root_id=body.root_id, parent=body.parent,
                                  kind='video' if body.operation == 'frames' else None)
            ids = [r['id'] for r in db.rows('SELECT m.id FROM media m WHERE ' + where, args)]
        elif ids:
            where, args = filters()
            visible = {row['id'] for row in db.rows(
                'SELECT m.id FROM media m WHERE ' + where +
                ' AND m.id IN (' + ','.join('?' for _ in ids) + ')',
                [*args, *ids],
            )}
            ids = [ident for ident in ids if ident in visible]
        ident = tasks.create(ids, body.force, body.operation)
        count = db.one('SELECT COUNT(*) AS n FROM tasks WHERE run_id=?', (ident,))['n']
        return {'id': ident, 'count': count}

    @app.get('/api/runs')
    def runs():
        result = db.rows('SELECT id,state,reason,created,kind FROM runs ORDER BY created DESC,rowid DESC LIMIT 30')
        for run in result:
            run['counts'] = db.rows('SELECT state,COUNT(*) AS count FROM tasks WHERE run_id=? GROUP BY state', (run['id'],))
            phases = {'extracting': 0, 'ai': 0}
            for task in db.rows("SELECT checkpoint FROM tasks WHERE run_id=? AND state='running'", (run['id'],)):
                try:
                    stage = json.loads(task['checkpoint']).get('work_stage')
                except (TypeError, json.JSONDecodeError):
                    continue
                if stage in phases:
                    phases[stage] += 1
            run['phases'] = phases
        visible_where, visible_args = filters()
        return {'runs': result, 'quota': worker_client.quota_state() if worker_client else worker.quota_state,
                'previews': db.rows('SELECT preview_state AS state,COUNT(*) AS count FROM media m WHERE ' + visible_where + ' GROUP BY preview_state', visible_args),
                'sync': db.rows('SELECT id,path,enabled,state,error FROM roots'),
                'vectors': db.rows('SELECT state,COUNT(*) AS count FROM vector_jobs GROUP BY state')}

    @app.delete('/api/runs/{ident}')
    async def delete_run(ident: str):
        if not db.execute("UPDATE runs SET state='deleting',reason='deleted' WHERE id=?", (ident,)):
            raise HTTPException(404, '任务不存在')
        db.execute("UPDATE tasks SET state='paused',reason='deleted' WHERE run_id=? AND state IN ('queued','running')", (ident,))
        await cancel_work(run_id=ident)
        with db.connect() as conn:
            conn.execute('DELETE FROM tasks WHERE run_id=?', (ident,))
            conn.execute('DELETE FROM runs WHERE id=?', (ident,))
        return {'ok': True}

    @app.get('/api/runs/{ident}/tasks')
    def run_tasks(ident: str):
        now = time.time()
        rows = db.rows(
            "SELECT t.id,t.media_id,m.name,m.kind,m.metadata,r.kind AS operation,t.state,t.reason,t.error,t.elapsed,t.updated,t.config,t.checkpoint "
            "FROM tasks t JOIN media m ON m.id=t.media_id JOIN runs r ON r.id=t.run_id "
            "WHERE t.run_id=? AND t.state IN ('queued','running','paused','failed') ORDER BY t.rowid",
            (ident,),
        )
        result = []
        for row in rows:
            item = {
                'id': row['id'],
                'media_id': row['media_id'],
                'name': row['name'],
                'kind': row['kind'],
                'operation': row['operation'],
                'state': row['state'],
                'reason': row['reason'],
                'error': row['error'],
                'elapsed': row['elapsed'] + (max(0, now - row['updated']) if row['state'] == 'running' else 0),
            }
            try:
                checkpoint = json.loads(row['checkpoint'])
            except (TypeError, json.JSONDecodeError):
                checkpoint = {}
            item['stage'] = checkpoint.get('work_stage')
            if row['kind'] == 'video':
                try:
                    config = json.loads(row['config'])
                    metadata = json.loads(row['metadata'])
                    frames = checkpoint.get('frames') or []
                    size = int(config['batch_size'])
                    overlap = int(config['batch_overlap'])
                    step = size - overlap
                    if checkpoint.get('scenes_done') and frames and size > 0 and step > 0:
                        total = 1 if len(frames) <= size else 1 + (len(frames) - size + step - 1) // step
                        item['batch'] = {
                            'current': min(len(checkpoint.get('batches') or []) + 1, total),
                            'total': total,
                        }
                    else:
                        item['batch'] = None
                    duration = float(metadata.get('duration') or 0)
                    interval = float(config['frame_interval'])
                    sample_count = max(1, math.ceil(duration / interval)) if duration > 0 and interval > 0 else 0
                    sample_done = min(int(checkpoint.get('sample_next') or 0), sample_count)
                    base_duration = duration * sample_done / sample_count if sample_count else 0
                    quality = config.get('video_frame_mode', 'quality') == 'quality'
                    scene_duration = duration if checkpoint.get('scenes_done') else min(
                        duration, max(0, int(checkpoint.get('scene_segment') or 0)) * 60)
                    completed_duration = base_duration + (scene_duration if quality else 0)
                    total_duration = duration * (2 if quality else 1)
                    item['extraction'] = {
                        'percent': min(100, round(completed_duration * 100 / total_duration)) if total_duration else 0,
                        'completed_duration': completed_duration,
                        'total_duration': total_duration,
                    }
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    item['batch'] = None
                    item['extraction'] = None
            result.append(item)
        return result

    @app.post('/api/runs/{ident}/{action}')
    async def control(ident: str, action: str):
        if db.one("SELECT id FROM runs WHERE id=? AND state='deleting'", (ident,)):
            raise HTTPException(409, '任务正在删除')
        if not db.one('SELECT id FROM runs WHERE id=?', (ident,)):
            raise HTTPException(404, '任务不存在')
        if action in {'pause', 'stop'}:
            tasks.pause(ident, immediate=action == 'stop')
            if action == 'stop':
                await cancel_work(run_id=ident)
        elif action in {'resume', 'retry'}:
            if action == 'retry':
                db.execute("UPDATE tasks SET state='paused' WHERE run_id=? AND state='failed'", (ident,))
            tasks.resume(ident)
        else:
            raise HTTPException(400, '未知操作')
        return {'ok': True}

    @app.get('/api/settings')
    def settings():
        return db.settings(public=True)

    @app.patch('/api/settings')
    def save_settings(body: dict):
        for key, value in body.items():
            if key not in DEFAULTS:
                raise HTTPException(400, '未知设置：' + key)
            if isinstance(DEFAULTS[key], int):
                if not isinstance(value, int) or isinstance(value, bool):
                    raise HTTPException(400, '数值设置必须为整数')
            elif not isinstance(value, str):
                raise HTTPException(400, '设置必须为文字')
        for key, label in (
            ('image_concurrency', '图片任务并发数'),
            ('cpu_decode_concurrency', 'CPU 解码并发数'),
            ('intel_decode_concurrency', 'Intel 核显解码并发数'),
            ('nvidia_decode_concurrency', 'NVIDIA 解码并发数'),
            ('ai_request_concurrency', 'AI 投送并发数'),
            ('embedding_request_concurrency', '向量生成并发数'),
        ):
            if key in body and body[key] < 1:
                raise HTTPException(400, label + '必须为正整数')
        if 'max_cpu_cores' in body:
            available_cores = os.cpu_count() or 1
            if not 0 <= body['max_cpu_cores'] <= available_cores:
                raise HTTPException(400, f'CPU 核心数必须为 0 到 {available_cores}；0 表示不限制')
        previous = db.settings()
        merged = {**previous, **body}
        # Never send a previous endpoint's secret to a newly configured host.
        for endpoint, secret in [('base_url','api_key'),('embedding_url','embedding_key')]:
            if endpoint in body and body[endpoint].rstrip('/') != previous[endpoint].rstrip('/') and secret not in body:
                body[secret] = ''
                merged[secret] = ''
        if merged['provider'] not in {'ollama', 'codex', 'openai'} or merged['embedding_provider'] not in {'ollama', 'openai'}:
            raise HTTPException(400, '不支持的服务')
        if merged['video_decoder'] not in {'cpu', 'intel', 'nvidia', 'intel_nvidia', 'cpu_intel_nvidia'}:
            raise HTTPException(400, '不支持的视频解码方式')
        if merged['video_frame_mode'] not in {'quality', 'fast'}:
            raise HTTPException(400, '不支持的视频抽帧模式')
        if not (merged['video_budget'] >= 0 and merged['request_timeout'] > 0 and merged['frame_interval'] > 0
                and merged['scene_fps'] > 0
                and 2 <= merged['batch_size'] <= 32 and 0 <= merged['batch_overlap'] < merged['batch_size']):
            raise HTTPException(400, '时间和抽帧配置超出范围')
        for name in ('base_url', 'embedding_url'):
            parsed = urlparse(merged[name])
            if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
                raise HTTPException(400, '服务地址必须为不含凭据的 HTTP(S) 地址')
        db.remember_credentials(previous)
        for key, value in body.items():
            db.set_setting(key, value)
        db.remember_credentials(merged)
        return db.settings(public=True)

    @app.get('/api/services/status')
    async def service_status(target: str = 'description'):
        config = db.settings()
        if target == 'embedding':
            config.update(provider=config['embedding_provider'],base_url=config['embedding_url'],api_key=config['embedding_key'])
        if config['provider'] == 'codex':
            try:
                codex = await worker.providers.get_codex()
                account = await codex.rpc('account/read', {'refreshToken': False})
                models = await codex.rpc('model/list', {})
                await worker.quota_check()
                return {'account': account, 'models': models, 'quota': worker.quota_state}
            except Exception:
                raise HTTPException(502, 'Codex 连接失败，请检查安装及登录状态') from None
        try:
            data = await worker.providers.request(config['base_url'], config['api_key'], '/api/tags' if config['provider'] == 'ollama' else '/models')
            return {'models': data}
        except Exception:
            raise HTTPException(502, '连接失败，请检查服务地址、密钥和网络') from None

    @app.post('/api/services/models')
    async def discover_models(body: dict):
        target = body.get('target', 'description')
        if target not in {'description', 'embedding'}:
            raise HTTPException(400, '未知模型用途')
        provider = body.get('provider')
        if provider not in ({'ollama','openai','codex'} if target=='description' else {'ollama','openai'}):
            raise HTTPException(400, '不支持的服务')
        try:
            if provider == 'codex':
                codex = await worker.providers.get_codex()
                account = await codex.rpc('account/read', {'refreshToken': False})
                records, cursor = [], None
                while True:
                    page = await codex.rpc('model/list', {'cursor':cursor} if cursor else {})
                    records.extend(page.get('data', []))
                    cursor = page.get('nextCursor')
                    if not cursor:
                        break
                return {'models': [{'id':m['model'], 'label':m.get('displayName') or m['model'],
                    'vision':'image' in m.get('inputModalities', []), 'text':True, 'embedding':False,
                    'efforts': [{'value':e['reasoningEffort'], 'description':e.get('description', '')}
                                for e in m.get('supportedReasoningEfforts', [])],
                    'default_effort':m.get('defaultReasoningEffort', '')}
                    for m in records if not m.get('hidden',False)],
                    'logged_in': bool(account.get('account')), 'message': '已复用本机 ChatGPT 登录' if account.get('account') else '尚未登录，请点击 ChatGPT 登录'}
            url = body.get('url', '')
            parsed = urlparse(url)
            if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password:
                raise HTTPException(400, '请填写有效的 HTTP(S) 服务地址')
            key = body.get('key', '')
            saved = db.settings()
            endpoint, secret = ('base_url','api_key') if target=='description' else ('embedding_url','embedding_key')
            if not key and saved[endpoint].rstrip('/') == url.rstrip('/'):
                key = saved[secret]
            if provider == 'openai':
                data = await worker.providers.request(url, key, '/models')
                return {'models':[{'id':m['id'],'label':m['id'],'vision':None,'text':None,'embedding':None} for m in data.get('data',[])],
                        'message':'已获取服务模型列表；兼容 API 通常不提供模型能力信息，请选择支持对应用途的模型'}
            data = await worker.providers.request(url, '', '/api/tags')
            models = []
            for model in data.get('models', []):
                ident = model.get('name') or model.get('model')
                info = await worker.providers.request(url, '', '/api/show', {'model':ident})
                capabilities = info.get('capabilities')
                models.append({'id':ident,'label':ident,
                    'vision': 'vision' in capabilities if capabilities is not None else None,
                    'text': 'completion' in capabilities if capabilities is not None else None,
                    'embedding':'embedding' in capabilities if capabilities is not None else None})
            return {'models':models,'message':'已获取本机已安装模型'}
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(502, '模型列表读取失败，请检查服务地址、凭据或 Codex 登录状态') from None

    @app.post('/api/services/codex/login')
    async def login():
        try:
            return await (await worker.providers.get_codex()).rpc('account/login/start', {'type': 'chatgpt'})
        except Exception:
            raise HTTPException(502, '无法启动 Codex 登录') from None

    @app.post('/api/vectors/{action}')
    def rebuild(action: str):
        config = db.settings()
        if action == 'rebuild':
            if not config['embedding_model']:
                raise HTTPException(400, '请先配置向量模型')
            safe = {k: v for k, v in config.items() if k not in {'api_key', 'embedding_key'}}
            with db.connect() as conn:
                conn.execute('INSERT OR REPLACE INTO rebuilds VALUES (?,?,?)', (profile(config), json.dumps(safe), 'running'))
                for row in conn.execute("SELECT id,revision FROM media WHERE description<>''").fetchall():
                    enqueue(conn, row['id'], row['revision'], config)
        elif action in {'resume', 'retry'}:
            db.execute("UPDATE vector_jobs SET state='queued',error=NULL WHERE state IN ('paused','failed')")
        elif action == 'pause':
            db.execute("UPDATE vector_jobs SET state='paused' WHERE state='queued'")
        else:
            raise HTTPException(400, '未知操作')
        return {'ok': True}

    @app.get('/api/search')
    async def search(q: str = Query(min_length=1), root_id: str | None = None, parent: str | None = None,
                     kind: str | None = None, status: str | None = None, after: float | None = None, before: float | None = None):
        config = db.settings()
        space = config['active_profile']
        if not space:
            raise HTTPException(409, '尚无有效向量索引，请先分析或重建向量')
        provider, url, model = json.loads(space)
        config.update(embedding_provider=provider, embedding_url=url, embedding_model=model)
        try:
            vector = await worker.providers.embed(config, q)
            where, args = filters(root_id, parent, kind, status, False, after, before)
            hits = await asyncio.to_thread(index.search, vector, space, where, args, 200)
        except Exception:
            raise HTTPException(502, '向量检索失败，请检查当前向量服务') from None
        if not hits:
            return {'items': [], 'total': 0}
        rows = db.rows('SELECT m.id,m.path,m.name,m.description,m.kind,m.sort_time,m.preview_state,m.analysis_state,m.vector_state,m.availability,' + ANALYSIS_ERROR_SQL + ' AS analysis_error FROM media m WHERE m.id IN (' + ','.join('?' for _ in hits) + ')', [h[0] for h in hits])
        for row in rows:
            row['title'] = display_title(row.pop('description', ''))
            analysis_status(row)
        mapping = {r['id']: r for r in rows}
        return {'items': [{**mapping[ident], 'score': score} for ident, score in hits if ident in mapping], 'total': len(hits)}

    static = Path(__file__).parent / 'static'
    if static.is_dir():
        app.mount('/', StaticFiles(directory=static, html=True), name='ui')
    return app
