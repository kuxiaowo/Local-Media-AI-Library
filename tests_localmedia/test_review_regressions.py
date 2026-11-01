import asyncio
import json
import os
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from localmedia.api import create_app
from localmedia.db import profile
from localmedia.index import VectorIndex, normalize
from localmedia.media import file_hash, normalized
from localmedia.providers import ProviderError
from localmedia.scanner import Scanner


@pytest.fixture
def library(tmp_path):
    app = create_app(tmp_path / 'cache', background=False)
    db, worker = app.state.db, app.state.worker
    source = tmp_path / 'photos'
    source.mkdir()
    path = source / 'sample.bmp'
    Image.new('RGB', (12, 10), 'red').save(path)
    os.utime(path, (time.time() - 100,) * 2)
    with TestClient(app) as client:
        assert client.post('/api/roots', json={'path': str(source)}).status_code == 200
        scanner = Scanner(db)
        scanner.scan()
        scanner.drain_hashes()
        row = db.one('SELECT * FROM media')
        scanner.preview(row)
        async def describe(*args, **kwargs):
            return 'AI description'
        async def embed(*args, **kwargs):
            return [1, 0]
        worker.providers.describe = describe
        worker.providers.embed = embed
        yield db, worker, scanner, client, path, row['id']


@pytest.mark.parametrize('edit_at', ['before_start', 'during_description', 'during_embedding'])
def test_manual_description_edit_wins_over_analysis(library, edit_at):
    db, worker, scanner, client, path, ident = library
    worker.tasks.create([ident])
    task = worker.tasks.claim()
    calls = []
    def edit():
        response = client.patch(f'/api/media/{ident}/description', json={'text': 'Saved by user'})
        assert response.status_code == 200
    async def describe(*args, **kwargs):
        calls.append('description')
        if edit_at == 'during_description':
            edit()
        return 'Stale AI description'
    async def embed(*args, **kwargs):
        calls.append('embedding')
        if edit_at == 'during_embedding':
            edit()
        return [1, 0]
    worker.providers.describe, worker.providers.embed = describe, embed
    if edit_at == 'before_start':
        edit()
    asyncio.run(worker.execute(task))
    media = db.one('SELECT description,revision,analysis_state FROM media')
    assert media == {'description': 'Saved by user', 'revision': 1, 'analysis_state': 'done'}
    assert db.one('SELECT revision FROM vector_jobs')['revision'] == 1
    failure = db.one('SELECT state,error FROM tasks')
    assert failure['state'] == 'failed' and '已保留当前结果' in failure['error']
    assert client.get(f'/api/media/{ident}').json()['analysis_state'] == 'done'
    if edit_at == 'before_start':
        assert calls == []


@pytest.mark.parametrize('same_size', [True, False])
def test_edited_then_moved_file_invalidates_cached_content(library, same_size):
    db, worker, scanner, client, path, ident = library
    db.execute("UPDATE media SET description='old red photo',note='keep note',analysis_state='done'")
    old = db.one('SELECT * FROM media')
    db.execute('INSERT INTO vectors VALUES (?,?,?,?,?)', (ident, profile(db.settings()), 0, 2, normalize([1, 0]).tobytes()))
    Image.new('RGB', (12 if same_size else 14, 10), 'blue').save(path)
    os.utime(path, (time.time() - 20,) * 2)
    moved = path.with_name('renamed.bmp')
    path.rename(moved)
    scanner.scan()
    row = db.one('SELECT * FROM media')
    assert row['id'] == ident and row['path'] == normalized(moved)
    assert row['hash'] is None and row['analysis_state'] == 'stale'
    assert row['preview_state'] == 'pending' and row['note'] == 'keep note'
    assert row['revision'] == old['revision'] + 1
    assert not db.rows('SELECT * FROM vectors')
    scanner.preview(row)
    scanner.drain_hashes()
    row = db.one('SELECT * FROM media')
    assert row['hash'] == file_hash(moved) and row['hash'] != old['hash']
    with Image.open(row['preview']) as preview:
        assert preview.getpixel((0, 0))[2] > 200


@pytest.mark.parametrize('switch_at', ['description', 'embedding', 'checkpoint'])
def test_task_publishes_in_current_embedding_space(library, switch_at):
    db, worker, scanner, client, path, ident = library
    db.set_setting('embedding_model', 'old')
    old = db.settings()
    db.set_setting('active_profile', profile(old))
    db.execute("UPDATE media SET description='Original description',analysis_state='done'")
    db.execute('INSERT INTO vectors VALUES (?,?,?,?,?)', (ident, profile(old), 0, 2, normalize([1, 0]).tobytes()))
    worker.tasks.create([ident], force=True)
    task = worker.tasks.claim()
    embeddings = []
    switched = False
    async def switch():
        nonlocal switched
        switched = True
        assert client.patch('/api/settings', json={'embedding_model': 'new'}).status_code == 200
        assert client.post('/api/vectors/rebuild').status_code == 200
        await worker.vector_job(db.one('SELECT * FROM vector_jobs'))
        assert json.loads(db.settings()['active_profile'])[2] == 'new'
    async def describe(*args, **kwargs):
        if switch_at == 'description':
            await switch()
        return 'New description'
    async def embed(config, text):
        embeddings.append((config['embedding_model'], text))
        if switch_at == 'embedding' and not switched:
            await switch()
        return [1, 0] if config['embedding_model'] == 'old' else [1, 0, 0]
    worker.providers.describe, worker.providers.embed = describe, embed
    async def scenario():
        if switch_at == 'checkpoint':
            # A pre-upgrade checkpoint did not record vector_profile explicitly.
            worker.tasks.checkpoint(task['id'], {'description': 'New description', 'vector': [1, 0]}, 1)
            task['checkpoint'] = db.one('SELECT checkpoint FROM tasks')['checkpoint']
            await switch()
        await worker.execute(task)
    asyncio.run(scenario())
    active = db.settings()['active_profile']
    assert db.one('SELECT state FROM tasks')['state'] == 'completed'
    assert db.one('SELECT vector_state FROM media')['vector_state'] == 'ready'
    vector = db.one('SELECT profile,dim,revision FROM vectors')
    assert vector == {'profile': active, 'dim': 3, 'revision': 1}
    assert ('new', 'New description') in embeddings
    assert VectorIndex(db).search([1, 0, 0], active)[0][0] == ident


def test_failure_in_new_embedding_space_keeps_published_result(library):
    db, worker, scanner, client, path, ident = library
    db.set_setting('embedding_model', 'old')
    db.execute("UPDATE media SET description='Keep this description',analysis_state='done',vector_state='ready'")
    worker.tasks.create([ident], force=True)
    task = worker.tasks.claim()
    new = {**db.settings(), 'embedding_model': 'new'}
    db.set_setting('active_profile', profile(new))
    db.execute('INSERT INTO vectors VALUES (?,?,?,?,?)', (ident, profile(new), 0, 3, normalize([1, 0, 0]).tobytes()))
    async def unavailable(config, text):
        assert config['embedding_model'] == 'new'
        raise ProviderError('New embedding service unavailable')
    worker.providers.embed = unavailable
    asyncio.run(worker.execute(task))
    assert db.one('SELECT description,revision FROM media') == {'description': 'Keep this description', 'revision': 0}
    assert db.one('SELECT profile FROM vectors')['profile'] == profile(new)
    assert db.one('SELECT state FROM tasks')['state'] == 'failed'


@pytest.mark.parametrize('restarted', [False, True])
@pytest.mark.parametrize('hash_before_resume', [False, True])
@pytest.mark.parametrize('legacy_snapshot', [False, True])
def test_hash_pending_task_resumes_without_false_source_change(library, restarted, hash_before_resume, legacy_snapshot):
    db, worker, scanner, client, path, ident = library
    db.execute('UPDATE media SET hash=NULL')
    run = worker.tasks.create([ident])
    if legacy_snapshot:
        config = json.loads(db.one('SELECT config FROM tasks')['config'])
        config.pop('source_size')
        config.pop('source_mtime_ns')
        db.execute('UPDATE tasks SET config=?', (json.dumps(config),))
    if restarted:
        db.restart_recovery()
    else:
        worker.tasks.pause(run)
    if hash_before_resume:
        scanner.drain_hashes()
    worker.tasks.resume(run)
    if not hash_before_resume:
        assert worker.tasks.claim() is None
        scanner.drain_hashes()
    task = worker.tasks.claim()
    assert task is not None and task['source_hash'] == file_hash(path)
    asyncio.run(worker.execute(task))
    assert db.one('SELECT state FROM tasks')['state'] == 'completed'


@pytest.mark.parametrize('paused', [False, True])
def test_unhashed_task_rejects_file_changed_before_first_hash(library, paused):
    db, worker, scanner, client, path, ident = library
    db.execute('UPDATE media SET hash=NULL')
    run = worker.tasks.create([ident])
    if paused:
        worker.tasks.pause(run)
    Image.new('RGB', (15, 10), 'blue').save(path)
    os.utime(path, (time.time() - 20,) * 2)
    scanner.scan()
    scanner.drain_hashes()
    if paused:
        worker.tasks.resume(run)
    assert worker.tasks.claim() is None
    assert db.one('SELECT state,error FROM tasks') == {'state': 'failed', 'error': '媒体内容改变，请重新分析'}


def test_failed_tasks_are_visible_and_cleared_by_new_attempt(library):
    db, worker, scanner, client, path, ident = library
    run = worker.tasks.create([ident])
    async def fail(*args, **kwargs):
        raise ProviderError('Model is not available')
    worker.providers.describe = fail
    asyncio.run(worker.execute(worker.tasks.claim()))
    assert db.one('SELECT analysis_state FROM media')['analysis_state'] == 'pending'
    # Derived status also works for failures recorded before this fix, with no migration.
    failed = client.get('/api/media', params={'status': 'failed'}).json()
    assert failed['total'] == 1
    assert failed['items'][0]['analysis_state'] == 'failed'
    assert failed['items'][0]['analysis_error'] == 'Model is not available'
    assert client.get('/api/media', params={'status': 'pending'}).json()['total'] == 0
    assert client.get(f'/api/media/{ident}').json()['analysis_error'] == 'Model is not available'
    tasks = client.get(f'/api/runs/{run}/tasks').json()
    assert tasks[0]['name'] == 'sample.bmp' and tasks[0]['error'] == 'Model is not available'
    worker.tasks.create([ident])
    assert client.get('/api/media', params={'status': 'failed'}).json()['total'] == 0
    assert client.get(f'/api/media/{ident}').json()['analysis_state'] == 'pending'


def test_frames_only_failure_does_not_replace_analysis_state(library):
    db, worker, scanner, client, path, ident = library
    db.execute("UPDATE media SET kind='video',analysis_state='done'")
    run = worker.tasks.create([ident], run_kind='frames')
    task = worker.tasks.claim()
    db.execute("UPDATE tasks SET state='failed',error='Cannot decode video' WHERE id=?", (task['id'],))
    assert client.get(f'/api/media/{ident}').json()['analysis_state'] == 'done'
    assert client.get(f'/api/runs/{run}/tasks').json()[0]['error'] == 'Cannot decode video'


def test_paused_task_reason_and_offline_settings_are_available(library):
    db, worker, scanner, client, path, ident = library
    run = worker.tasks.create([ident])
    worker.tasks.pause(run)
    row = client.get(f'/api/runs/{run}/tasks').json()[0]
    assert row['state'] == 'paused' and row['reason'] == 'manual'
    db.set_setting('base_url', 'http://127.0.0.1:1')
    db.set_setting('embedding_url', 'http://127.0.0.1:1')
    response = client.patch('/api/settings', json={'video_frame_mode': 'fast', 'max_cpu_cores': 1})
    assert response.status_code == 200
    assert response.json()['video_frame_mode'] == 'fast'


def test_obsolete_vector_job_cannot_mark_active_space_ready(library):
    from localmedia.index import enqueue
    db, worker, scanner, client, path, ident = library
    old = {**db.settings(), 'embedding_model': 'old'}
    new = {**old, 'embedding_model': 'new'}
    db.set_setting('active_profile', profile(new))
    db.execute("UPDATE media SET description='Description',vector_state='pending'")
    with db.connect() as conn:
        enqueue(conn, ident, 0, old)
        enqueue(conn, ident, 0, new)
    asyncio.run(worker.vector_job(db.one('SELECT * FROM vector_jobs WHERE profile=?', (profile(old),))))
    assert db.one('SELECT vector_state FROM media')['vector_state'] == 'pending'
    asyncio.run(worker.vector_job(db.one('SELECT * FROM vector_jobs WHERE profile=?', (profile(new),))))
    assert db.one('SELECT vector_state FROM media')['vector_state'] == 'ready'


def test_vector_job_does_not_publish_after_description_edit(library):
    db, worker, scanner, client, path, ident = library
    assert client.patch(f'/api/media/{ident}/description', json={'text': 'First edit'}).status_code == 200
    job = db.one('SELECT * FROM vector_jobs')
    async def embed(*args, **kwargs):
        assert client.patch(f'/api/media/{ident}/description', json={'text': 'Latest edit'}).status_code == 200
        return [1, 0]
    worker.providers.embed = embed
    asyncio.run(worker.vector_job(job))
    assert not db.rows('SELECT * FROM vectors')
    assert db.one('SELECT description,vector_state FROM media') == {'description': 'Latest edit', 'vector_state': 'pending'}
    assert db.one('SELECT revision FROM vector_jobs')['revision'] == 2
