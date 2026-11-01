import asyncio
import contextlib
import io
import os
import sqlite3
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from localmedia.api import create_app
from localmedia.db import Database, SCHEMA
from localmedia.media import ThumbnailUnavailable, make_preview, normalized, run_preview_process
from localmedia.scanner import Scanner


def photo(path, size=(1200, 800), color='red'):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', size, color).save(path)
    os.utime(path, (time.time() - 20,) * 2)
    return path


@pytest.fixture
def library(tmp_path):
    app = create_app(tmp_path / 'cache', background=False)
    with TestClient(app) as client:
        source = tmp_path / 'photos'
        source.mkdir()
        root = client.post('/api/roots', json={'path': str(source)}).json()['id']
        yield app.state.db, app.state.scanner, client, source, root


def wait_until(predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, 'background work did not finish'
        threading.Event().wait(.02)


def test_scan_batches_writes_and_unchanged_scan_does_not_update_media(library, monkeypatch):
    db, scanner, client, source, root = library
    for i in range(180):
        photo(source / f'{i}.jpg', (8, 8))
    statements, connections = [], []
    original = db.connect
    @contextlib.contextmanager
    def tracked():
        connections.append(1)
        with original() as conn:
            conn.set_trace_callback(statements.append)
            yield conn
    monkeypatch.setattr(db, 'connect', tracked)
    scanner.scan()
    assert db.one('SELECT COUNT(*) n FROM media')['n'] == 180
    assert len(connections) < 20
    assert sum(s == 'COMMIT' for s in statements) < 15
    plan = db.rows('EXPLAIN QUERY PLAN SELECT path FROM media WHERE size=? AND hash IS NOT NULL LIMIT 32', (999999,))
    assert any('media_size_hashed' in r['detail'] for r in plan)
    statements.clear()
    scanner.scan()
    assert not any(s.startswith('UPDATE media') for s in statements)


def test_first_file_is_visible_before_inventory_finishes(library, monkeypatch):
    db, scanner, client, source, root = library
    for i in range(5):
        photo(source / f'{i}.jpg')
    original = scanner.discover
    calls = []
    def discover(conn, owner, path):
        if calls:
            assert db.one('SELECT COUNT(*) n FROM media')['n'] >= 1
            assert scanner.preview_wake.is_set()
        original(conn, owner, path)
        calls.append(path)
    monkeypatch.setattr(scanner, 'discover', discover)
    scanner.scan()
    assert len(calls) == 5


def test_thumbnail_then_standard_preserves_dimensions_and_ai_input(library):
    db, scanner, client, source, root = library
    photo(source / 'large.jpg', (3200, 2400))
    scanner.scan()
    row = db.one('SELECT * FROM media')
    scanner.preview(row, 'thumbnail')
    small = db.one('SELECT * FROM media')
    assert small['thumbnail_state'] == 'ready' and small['preview_state'] == 'pending'
    with Image.open(small['thumbnail']) as image:
        assert max(image.size) == 400
    detail = client.get('/api/media/' + row['id']).json()
    assert (detail['metadata']['width'], detail['metadata']['height']) == (3200, 2400)
    assert client.get('/api/media/' + row['id'] + '/preview').status_code == 200
    first_version = detail['preview_version']
    with open(small['thumbnail'], 'rb') as browser_reader:
        scanner.preview(small)
        assert browser_reader.read(2) == b'\xff\xd8'
    ready = db.one('SELECT * FROM media')
    assert ready['preview_state'] == 'ready' and ready['preview'] != ready['thumbnail']
    assert ready['thumbnail'] != small['thumbnail']
    with Image.open(ready['preview']) as image:
        assert max(image.size) == 2000
    assert client.get('/api/media/' + row['id']).json()['preview_version'] != first_version
    scanner.drain_hashes()
    worker = client.app.state.worker
    seen = []
    async def describe(config, prompt, images=(), **kwargs):
        seen.extend(images)
        return 'synthetic description'
    async def embed(*args):
        return [1, 0]
    worker.providers.describe, worker.providers.embed = describe, embed
    worker.tasks.create([row['id']])
    asyncio.run(worker.execute(worker.tasks.claim()))
    assert seen == [ready['preview']]


def test_fast_jpeg_rotation_keeps_original_dimensions(tmp_path):
    path = tmp_path / 'rotated.jpg'
    image = Image.new('RGB', (2400, 1600), 'red')
    exif = Image.Exif()
    exif[274] = 6
    image.save(path, exif=exif)
    target = tmp_path / 'thumbnail.jpg'
    meta = make_preview(path, target, 'image', thumbnail=True)
    assert (meta['width'], meta['height']) == (1600, 2400)
    with Image.open(target) as result:
        assert result.height == 400 and result.width < result.height


@pytest.mark.parametrize('embedded', [True, False])
def test_raw_quick_path_never_unpacks_full_raw(tmp_path, monkeypatch, embedded):
    import rawpy
    import exifread
    data = io.BytesIO()
    Image.new('RGB', (800, 600), 'blue').save(data, format='JPEG')
    class Raw:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def open_file(self, path): pass
        def extract_thumb(self):
            if not embedded:
                raise rawpy.LibRawNoThumbnailError('no embedded preview')
            return type('Thumb', (), {'format': rawpy.ThumbFormat.JPEG, 'data': data.getvalue()})()
        def postprocess(self, **kwargs):
            raise AssertionError('quick preview must not decode RAW pixels')
    monkeypatch.setattr(rawpy, 'RawPy', Raw)
    monkeypatch.setattr(exifread, 'process_file', lambda *args, **kwargs: {})
    path = tmp_path / 'image.nef'
    path.write_bytes(b'synthetic raw container')
    if embedded:
        make_preview(path, tmp_path / 'thumb.jpg', 'image', thumbnail=True)
        assert (tmp_path / 'thumb.jpg').is_file()
    else:
        with pytest.raises(ThumbnailUnavailable):
            make_preview(path, tmp_path / 'thumb.jpg', 'image', thumbnail=True)


def test_legacy_database_reuses_existing_standard_preview(tmp_path):
    directory = tmp_path / 'cache'
    directory.mkdir()
    preview = photo(directory / 'existing.jpg')
    with sqlite3.connect(directory / 'library.sqlite3') as conn:
        conn.executescript(SCHEMA.replace(" thumbnail TEXT, thumbnail_state TEXT NOT NULL DEFAULT 'pending',\n", ''))
        conn.execute("INSERT INTO roots(id,path) VALUES ('r','root')")
        conn.execute("INSERT INTO media(id,root_id,path,parent,name,kind,preview,preview_state,updated) VALUES ('m','r','image.jpg','root','image.jpg','image',?,'ready',0)", (str(preview),))
    db = Database(directory)
    assert db.one('SELECT thumbnail,thumbnail_state FROM media') == {'thumbnail': str(preview), 'thumbnail_state': 'ready'}
    assert Scanner(db).previews.choose('image') is None


def test_viewport_priority_detail_upgrade_expiry_and_background_fairness(library):
    db, scanner, client, source, root = library
    for i in range(4):
        photo(source / f'{i}.jpg')
    scanner.scan()
    rows = db.rows('SELECT * FROM media ORDER BY name')
    visible = rows[-1]
    scheduler = scanner.previews
    assert client.post('/api/previews/priority', json={'client':'tab', 'ids':[visible['id']]}).json() == {'accepted': 1}
    assert scheduler.choose('image')[0]['id'] == visible['id']
    assert scheduler.choose('image')[1] == 'thumbnail'
    scanner.preview(visible, 'thumbnail')
    assert scheduler.choose('image')[1] == 'standard'
    scheduler.bursts['image'] = 8
    assert scheduler.choose('image')[0]['id'] != visible['id']
    scheduler.request('tab', [], rows[1]['id'])
    assert scheduler.choose('image')[0]['id'] == rows[1]['id']
    assert scheduler.choose('image')[1] == 'standard'
    scheduler.clients['tab'] = (time.monotonic() - 1, [visible['id']], None)
    assert scheduler.priorities() == ([], set())
    assert scheduler.choose('image')[0]['id'] == rows[0]['id']
    assert client.patch('/api/settings', json={'preview_image_concurrency':9}).status_code == 400
    assert client.patch('/api/settings', json={'preview_video_concurrency':0}).status_code == 400
    assert client.post('/api/previews/priority', json={'client':'tab','ids':['missing']}).json()['accepted'] == 0
    assert client.patch(f'/api/roots/{root}/enabled', json={'enabled':False}).status_code == 200
    assert client.post('/api/previews/priority', json={'client':'tab','ids':[visible['id']]}).json()['accepted'] == 0


def test_large_viewport_does_not_starve_background_files(library):
    db, scanner, client, source, root = library
    for i in range(45): photo(source / f'{i:02}.jpg', (8, 8))
    scanner.scan()
    rows = db.rows('SELECT id FROM media ORDER BY updated,id')
    visible = [row['id'] for row in rows[:40]]
    scanner.previews.request('tab', visible)
    scanner.previews.bursts['image'] = 8
    assert scanner.previews.choose('image')[0]['id'] not in visible


def test_image_lane_finishes_while_video_is_blocked(library, monkeypatch):
    db, scanner, client, source, root = library
    for i in range(4): photo(source / f'{i}.jpg')
    video = source / 'slow.mp4'
    video.write_bytes(b'synthetic video')
    os.utime(video, (time.time() - 20,) * 2)
    scanner.scan()
    release_images, release_video = threading.Event(), threading.Event()
    lock = threading.Lock()
    current = {'image':0, 'video':0}
    peak = {'image':0, 'video':0}
    def render(path, destination, kind, *args, **kwargs):
        with lock:
            current[kind] += 1
            peak[kind] = max(peak[kind], current[kind])
        try:
            assert (release_video if kind == 'video' else release_images).wait(8)
            Image.new('RGB', (500, 400), 'red').save(destination)
            return {'width':500, 'height':400}
        finally:
            with lock: current[kind] -= 1
    monkeypatch.setattr('localmedia.scanner.make_preview', render)
    thread = threading.Thread(target=scanner.preview_loop)
    thread.start()
    try:
        wait_until(lambda: current == {'image':2,'video':1})
        release_images.set()
        wait_until(lambda: db.one("SELECT COUNT(*) n FROM media WHERE kind='image' AND preview_state='ready'")['n'] == 4)
        assert db.one("SELECT preview_state FROM media WHERE kind='video'")['preview_state'] == 'pending'
        assert peak == {'image':2, 'video':1}
    finally:
        release_images.set(); release_video.set()
        scanner.stop.set(); scanner.preview_wake.set()
        thread.join(10)
    assert not thread.is_alive()


@pytest.mark.parametrize('change', ['remove', 'modify'])
def test_inflight_preview_cannot_publish_removed_or_changed_source(library, monkeypatch, change):
    db, scanner, client, source, root = library
    path = photo(source / 'one.jpg')
    scanner.scan()
    row = db.one('SELECT * FROM media')
    started, release = threading.Event(), threading.Event()
    def render(path, destination, *args, **kwargs):
        Image.new('RGB', (8, 8), 'red').save(destination)
        started.set()
        assert release.wait(5)
        return {'width':8,'height':8}
    monkeypatch.setattr('localmedia.scanner.make_preview', render)
    thread = threading.Thread(target=scanner.preview, args=(row,))
    thread.start()
    try:
        assert started.wait(3)
        if change == 'remove':
            assert client.post('/api/directories/remove', json={'path':str(source)}).status_code == 200
        else:
            photo(path, (1300, 900), 'blue')
            scanner.scan()
    finally:
        release.set(); thread.join(5)
    assert path.exists() and not thread.is_alive()
    if change == 'modify':
        assert db.one('SELECT preview_state,preview FROM media') == {'preview_state':'pending','preview':None}
    else:
        assert not db.rows('SELECT * FROM media')
    assert not list((db.directory / 'previews').glob('*.jpg'))


def test_hashes_do_not_wait_for_other_medias_standard_previews(library):
    db, scanner, client, source, root = library
    photo(source / 'ready.jpg'); photo(source / 'pending.jpg')
    scanner.scan()
    ready = db.one("SELECT * FROM media WHERE name='ready.jpg'")
    scanner.preview(ready)
    thread = threading.Thread(target=scanner.hash_loop)
    thread.start()
    try:
        wait_until(lambda: db.one("SELECT hash FROM media WHERE name='ready.jpg'")['hash'] is not None)
        assert db.one("SELECT hash FROM media WHERE name='pending.jpg'")['hash'] is None
    finally:
        scanner.stop.set(); scanner.hash_wake.set()
        thread.join(5)
    assert not thread.is_alive()


def test_preview_subprocess_can_be_cancelled():
    cancel = threading.Event()
    timer = threading.Timer(.15, cancel.set)
    timer.start()
    try:
        with pytest.raises(InterruptedError):
            run_preview_process([sys.executable, '-c', 'import time;time.sleep(60)'], 5, cancel.is_set)
    finally:
        timer.cancel()


def test_delayed_cleanup_keeps_current_two_level_previews(library):
    db, scanner, client, source, root = library
    photo(source / 'current.jpg')
    scanner.scan()
    row = db.one('SELECT * FROM media')
    scanner.preview(row)
    ready = db.one('SELECT * FROM media')
    obsolete = db.directory / 'previews' / (row['id'] + '-obsolete.jpg')
    obsolete.write_bytes(b'obsolete synthetic preview')
    db.execute('INSERT INTO preview_cleanup VALUES (?,0)', (row['id'],))
    scanner.cleanup_previews()
    assert not obsolete.exists()
    from pathlib import Path
    assert Path(ready['preview']).exists() and Path(ready['thumbnail']).exists()
