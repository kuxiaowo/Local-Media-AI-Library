"""Opt-in Lightroom adapter over the existing scanner, task queue and search.

Each catalog uses its own service/database. The small percent-encoded text
protocol works with Lightroom 13's SDK without evaluating Lua or shipping a
third-party JSON parser. Original files and LR catalog databases are untouched.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
import ntpath
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from PIL import Image

from .media import normalized


def catalog_id(path):
    return hashlib.sha256(ntpath.normcase(ntpath.normpath(str(path))).encode('utf-8')).hexdigest()


def install_bridge(app, db, scanner, config, create_run, search, analysis_error_sql):
    identity, token = config['catalog_id'], config['token']
    inbox = Path(config['inbox']).resolve()
    if len(token) < 32 or len(identity) != 64:
        raise ValueError('Invalid Lightroom bridge configuration')
    if inbox == db.directory or db.directory in inbox.parents or inbox in db.directory.parents:
        raise ValueError('Lightroom renders must be outside the AI database directory')
    binding = db.one("SELECT value FROM settings WHERE key='lightroom_catalog_id'")
    if binding and json.loads(binding['value']) != identity:
        raise ValueError('This AI database belongs to another Lightroom catalog')
    db.set_setting('lightroom_catalog_id', identity)
    inbox.mkdir(parents=True, exist_ok=True)
    root_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'localmedia:lightroom:' + identity))
    db.execute('INSERT OR IGNORE INTO roots(id,path) VALUES (?,?)', (root_id, normalized(inbox)))
    root = db.one('SELECT path FROM roots WHERE id=?', (root_id,))
    if not root or root['path'] != normalized(inbox):
        raise ValueError('Lightroom render directory does not match this database')
    db.execute('CREATE TABLE IF NOT EXISTS lightroom_photos (photo_uuid TEXT PRIMARY KEY, media_id TEXT UNIQUE NOT NULL REFERENCES media(id) ON DELETE CASCADE)')

    def authorize(request: Request):
        supplied = request.headers.get('x-localmedia-token', '')
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(403, 'Lightroom connection token mismatch')
        if request.headers.get('x-localmedia-catalog') != identity:
            raise HTTPException(409, 'Wrong Lightroom catalog')

    router = APIRouter(prefix='/api/lightroom', dependencies=[Depends(authorize)],
                       default_response_class=PlainTextResponse)

    def photo_key(value):
        try:
            return str(uuid.UUID(value))
        except (ValueError, AttributeError):
            raise HTTPException(400, 'Invalid photo UUID') from None

    def media(photo):
        row = db.one('SELECT m.* FROM lightroom_photos p JOIN media m ON p.media_id=m.id '
                     'JOIN roots r ON r.id=m.root_id WHERE p.photo_uuid=? AND r.enabled=1 '
                     "AND m.availability='online'", (photo_key(photo),))
        if not row:
            raise HTTPException(404, 'Export this photo to AI first')
        return row

    @router.get('/health')
    def health():
        return 'ok\n' + identity

    @router.get('/policy')
    def policy():
        settings = db.settings()
        embedding = json.loads(settings['active_profile'])[0] if settings['active_profile'] else settings['embedding_provider']
        return 'ok\n' + ('cloud' if settings['provider'] in {'openai', 'codex'} or embedding == 'openai' else 'local')

    def ingest_bytes(key, data, filename):
        try:
            with Image.open(io.BytesIO(data)) as picture:
                if picture.format != 'JPEG' or max(picture.size) > 4096:
                    raise ValueError('Expected a JPEG preview no larger than 4096 pixels')
                picture.verify()
        except Exception:
            raise HTTPException(400, 'Invalid JPEG preview') from None
        path = inbox / (key + '.jpg')
        temporary = inbox / ('.' + uuid.uuid4().hex + '.tmp')
        try:
            with scanner.lock, scanner.preview_lock:
                root = db.one('SELECT enabled FROM roots WHERE id=?', (root_id,))
                if not root or not root['enabled']:
                    raise HTTPException(409, 'The AI library is disabled')
                current = db.one('SELECT * FROM media WHERE path=?', (normalized(path),))
                same = path.exists() and hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(data).digest()
                if not same:
                    if current and db.one("SELECT id FROM tasks WHERE media_id=? AND state IN ('queued','running','paused')", (current['id'],)):
                        raise HTTPException(409, 'Wait for or cancel the previous analysis before updating this preview')
                    temporary.write_bytes(data)
                    temporary.replace(path)
                scanner.visit(root_id, path, complete=True)
                row = db.one('SELECT * FROM media WHERE path=?', (normalized(path),))
                if filename:
                    db.execute('UPDATE media SET name=? WHERE id=?', (filename, row['id']))
                db.execute('INSERT INTO lightroom_photos VALUES (?,?) ON CONFLICT(photo_uuid) DO UPDATE SET media_id=excluded.media_id', (key, row['id']))
            # The SDK upload is complete. Finish its small preview before making
            # analysis claimable; the generic scanner otherwise waits for stable files.
            if row['preview_state'] != 'ready':
                scanner.preview(row)
            updated = db.execute("UPDATE media SET hash=? WHERE id=? AND size=? AND mtime_ns=? AND preview_state='ready'",
                                 (hashlib.sha256(data).hexdigest(), row['id'], row['size'], row['mtime_ns']))
            if not updated:
                raise HTTPException(409, 'Preview changed or could not be decoded; export it again')
            return 'ok\n' + row['id']
        finally:
            temporary.unlink(missing_ok=True)

    @router.post('/ingest')
    async def ingest(request: Request, photo: str, filename: str = Query(default='', max_length=255)):
        key = photo_key(photo)
        if filename and (ntpath.basename(filename) != filename or any(ord(c) < 32 for c in filename)):
            raise HTTPException(400, 'Expected a filename without directory information')
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 16 * 1024 * 1024:
                raise HTTPException(413, 'Preview is too large')
            chunks.append(chunk)
        return await asyncio.to_thread(ingest_bytes, key, b''.join(chunks), filename)

    @router.post('/analyze')
    def analyze(photo: str, cloud_ack: bool = False):
        result = create_run([media(photo)['id']], cloud_ack)
        return 'ok\n' + result['id'] + '\n' + str(result['count'])

    @router.get('/description')
    def description(photo: str):
        row = media(photo)
        error = db.one('SELECT ' + analysis_error_sql + ' AS error FROM media m WHERE m.id=?', (row['id'],))['error']
        if error:
            return 'ok\nfailed\n' + quote(error, safe='')
        return 'ok\n' + row['analysis_state'] + '\n' + quote(row['description'], safe='')

    @router.get('/search')
    async def find(q: str = Query(min_length=1, max_length=2000)):
        result = await search(q=q, root_id=root_id)
        mapping = {row['media_id']: row['photo_uuid'] for row in db.rows('SELECT * FROM lightroom_photos')}
        return 'ok\n' + '\n'.join(mapping[row['id']] + '\t' + quote(row.get('title', ''), safe='')
                                  for row in result['items'] if row['id'] in mapping)

    app.include_router(router)
