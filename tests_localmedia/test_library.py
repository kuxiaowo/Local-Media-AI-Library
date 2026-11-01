import asyncio
import json
import os
import shutil
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from localmedia.api import create_app
from localmedia.db import Database, profile
from localmedia.index import VectorIndex, normalize
from localmedia.media import make_preview, normalized
from localmedia.scanner import Scanner
from localmedia.tasks import Tasks, Paused
from localmedia.worker import Worker


def picture(path, color='red'):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (120, 80), color).save(path)
    os.utime(path, (time.time()-10, time.time()-10))
    return path


@pytest.fixture
def library(tmp_path):
    db = Database(tmp_path/'cache')
    source = tmp_path/'photos'
    source.mkdir()
    db.execute('INSERT INTO roots(id,path) VALUES (?,?)', ('r', normalized(source)))
    scanner = Scanner(db)
    return db, scanner, source


def annotate(db):
    row = db.one('SELECT * FROM media')
    db.execute("UPDATE media SET description='旧描述',note='备注',analysis_state='done',vector_state='ready' WHERE id=?", (row['id'],))
    vector = normalize([1,2,3])
    db.execute('INSERT INTO vectors VALUES (?,?,?,?,?)', (row['id'], profile(db.settings()), 0, 3, vector.tobytes()))
    return row['id']


@pytest.mark.parametrize('operation', ['rename', 'subfolder', 'cross_root', 'relocate'])
def test_move_preserves_identity_and_annotations(library, operation):
    db, scanner, source = library
    file = picture(source/'a.jpg')
    scanner.scan();scanner.drain_hashes()
    ident = annotate(db)
    if operation == 'rename':
        file.rename(source/'b.jpg')
    elif operation == 'subfolder':
        (source/'sub').mkdir()
        file.rename(source/'sub/b.jpg')
    else:
        other = source.parent/'other'
        if operation == 'relocate':
            source.rename(other)
            db.execute("UPDATE roots SET path=?,state='pending' WHERE id='r'", (normalized(other),))
        else:
            other.mkdir()
            db.execute('INSERT INTO roots(id,path) VALUES (?,?)', ('r2', normalized(other)))
            file.rename(other/'b.jpg')
    scanner.scan();scanner.drain_hashes()
    rows = db.rows('SELECT * FROM media')
    assert len(rows) == 1
    assert rows[0]['id'] == ident and rows[0]['note'] == '备注'
    assert rows[0]['description'] == '旧描述' and rows[0]['availability'] == 'online'
    assert db.one('SELECT * FROM vectors')['media_id'] == ident


def test_duplicate_is_distinct(library):
    db, scanner, source = library
    file = picture(source/'a.jpg')
    scanner.scan();scanner.drain_hashes()
    annotate(db)
    shutil.copy2(file, source/'b.jpg')
    scanner.scan();scanner.drain_hashes()
    assert len(db.rows('SELECT * FROM media')) == 2
    assert db.one("SELECT * FROM media WHERE name='b.jpg'")['description'] == ''


def test_ambiguous_cross_volume_copies_do_not_merge(library):
    db, scanner, source = library
    file = picture(source/'a.jpg')
    scanner.scan();scanner.drain_hashes()
    ident = annotate(db)
    db.execute("UPDATE media SET fsid='different-volume'")
    shutil.copy2(file, source/'b.jpg')
    shutil.copy2(file, source/'c.jpg')
    file.unlink()
    scanner.scan();scanner.drain_hashes()
    assert len(db.rows('SELECT * FROM media')) == 3
    assert db.one('SELECT availability FROM media WHERE id=?', (ident,))['availability'] == 'missing'


def test_unique_cross_volume_move_merges_after_background_hash(library):
    db, scanner, source = library
    file = picture(source/'a.jpg')
    scanner.scan();scanner.drain_hashes()
    ident = annotate(db)
    db.execute("UPDATE media SET fsid='different-volume'")
    file.rename(source/'moved.jpg')
    scanner.scan()
    provisional = db.one("SELECT * FROM media WHERE name='moved.jpg'")
    assert provisional['id'] != ident and provisional['hash'] is None
    scanner.drain_hashes()
    rows = db.rows('SELECT * FROM media')
    assert len(rows) == 1 and rows[0]['id'] == ident
    assert rows[0]['description'] == '旧描述' and rows[0]['note'] == '备注'


def test_same_path_with_unchanged_size_and_mtime_is_not_rehashed(library):
    db, scanner, source = library
    file = picture(source/'a.bmp')
    scanner.scan();scanner.drain_hashes()
    ident = annotate(db)
    stat = file.stat()
    picture(file, 'blue')
    os.utime(file, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    scanner.scan()
    row = db.one('SELECT * FROM media')
    assert row['id'] == ident and row['analysis_state'] == 'done'
    assert row['description'] == '旧描述'
    assert db.rows('SELECT * FROM vectors')


def test_inaccessible_root_never_marks_missing(library):
    db, scanner, source = library
    picture(source/'a.jpg')
    scanner.scan()
    source.rename(source.parent/'unmounted')
    scanner.scan()
    assert db.one('SELECT availability FROM media')['availability'] == 'online'
    assert db.one('SELECT state FROM roots')['state'] == 'offline'


def test_missing_and_reappearance(library):
    db, scanner, source = library
    file = picture(source/'a.jpg')
    scanner.scan();scanner.drain_hashes()
    ident = annotate(db)
    backup = file.read_bytes()
    file.unlink()
    scanner.scan()
    assert db.one('SELECT availability FROM media')['availability'] == 'missing'
    file.write_bytes(backup)
    os.utime(file, (time.time()-10,)*2)
    scanner.scan()
    assert db.one('SELECT id,availability FROM media') == {'id':ident,'availability':'online'}


def test_corrupt_preview_does_not_abort_batch(library):
    db, scanner, source = library
    picture(source/'valid.jpg')
    (source/'broken.nef').write_bytes(b'not a raw photo')
    os.utime(source/'broken.nef', (time.time()-10,)*2)
    scanner.scan()
    for row in db.rows('SELECT * FROM media'):
        scanner.preview(row)
    assert {r['preview_state'] for r in db.rows('SELECT * FROM media')} == {'ready','failed'}


def test_orientation_and_heic(tmp_path):
    image = Image.new('RGB', (100,60), 'green')
    exif = Image.Exif(); exif[274] = 6
    image.save(tmp_path/'rotated.jpg',exif=exif)
    meta = make_preview(tmp_path/'rotated.jpg',tmp_path/'preview.jpg','image')
    assert (meta['width'],meta['height']) == (60,100)
    import pillow_heif
    pillow_heif.register_heif_opener()
    image.save(tmp_path/'photo.heic')
    meta = make_preview(tmp_path/'photo.heic',tmp_path/'heic.jpg','image')
    assert (meta['width'],meta['height']) == (100,60)


def test_index_revision_invalidation_and_dimension_isolation(library):
    db, scanner, source = library
    picture(source/'a.jpg');scanner.scan();ident=annotate(db)
    index = VectorIndex(db); space = profile(db.settings())
    assert index.search([1,2,3],space)[0][0] == ident
    assert index.search([1,2],space) == []
    db.execute('UPDATE media SET revision=1 WHERE id=?',(ident,))
    assert index.search([1,2,3],space) == []


def test_api_key_redaction_and_description_updates(tmp_path):
    app = create_app(tmp_path/'db', background=False)
    db=app.state.db
    source=tmp_path/'photos';picture(source/'a.jpg')
    with TestClient(app) as client:
        assert client.post('/api/roots',json={'path':str(source)}).status_code == 200
        scanner=Scanner(db);scanner.scan()
        ident=db.one('SELECT id FROM media')['id']
        response=client.patch('/api/settings',json={'api_key':'SECRET', 'embedding_key':'EMBED_SECRET'})
        assert 'SECRET' not in response.text
        assert response.json()['api_key_configured']
        assert client.patch('/api/media/'+ident+'/description',json={'text':'手写描述'}).status_code==200
        assert db.one('SELECT * FROM vector_jobs')['state']=='queued'
        assert client.get('/api/media?limit=201').status_code==422
        assert client.post('/api/sync',json={},headers={'Origin':'https://hostile.example'}).status_code==403
        assert client.delete('/api/media/'+ident+'/missing-record').status_code==409
        assert client.patch('/api/settings',json={'batch_size':6,'batch_overlap':6}).status_code==400
        assert client.patch('/api/settings',json={'scene_fps':0}).status_code==400
        assert client.patch('/api/settings',json={'scene_fps':3}).json()['scene_fps']==3
        assert client.patch('/api/settings',json={'video_decoder':'invalid'}).status_code==400
        assert client.patch('/api/settings',json={'video_decoder':'nvidia'}).json()['video_decoder']=='nvidia'
        assert client.patch('/api/settings',json={'video_decoder':'intel_nvidia'}).json()['video_decoder']=='intel_nvidia'
        assert client.patch('/api/settings',json={'video_decoder':'cpu_intel_nvidia'}).json()['video_decoder']=='cpu_intel_nvidia'
        assert client.patch('/api/settings',json={'video_frame_mode':'invalid'}).status_code==400
        assert client.patch('/api/settings',json={'video_frame_mode':'fast'}).json()['video_frame_mode']=='fast'


def test_lan_host_can_be_explicitly_allowed(tmp_path):
    app = create_app(
        tmp_path / 'db',
        background=False,
        allowed_hosts=['localhost', '127.0.0.1', '192.168.1.18'],
    )
    with TestClient(app, base_url='http://192.168.1.18') as client:
        assert client.get('/api/health').status_code == 200
    with TestClient(app, base_url='http://hostile.example') as client:
        assert client.get('/api/health').status_code == 400


def test_api_fixed_scope_cloud_ack_and_old_description(library):
    db, scanner, source = library
    picture(source/'a.jpg');scanner.scan();annotate(db)
    app=create_app(db.directory,background=False)
    with TestClient(app) as client:
        client.patch('/api/settings',json={'provider':'codex'})
        assert client.post('/api/runs',json={'force':True}).status_code==400
        response=client.post('/api/runs',json={'force':True,'cloud_ack':True})
        assert response.status_code==200
        picture(source/'b.jpg');scanner.scan()
        assert len(db.rows('SELECT * FROM tasks'))==1
        assert db.one("SELECT description FROM media WHERE name='a.jpg'")['description']=='旧描述'


def test_worker_atomic_success_and_failed_embedding_preserves_description(library):
    db,scanner,source=library
    picture(source/'a.jpg');scanner.scan();scanner.drain_hashes();ident=annotate(db)
    scanner.preview(db.one('SELECT * FROM media'))
    tasks=Tasks(db);worker=Worker(db,tasks)
    async def describe(*args,**kwargs):return '新标题\n新概述\n新详细描述'
    async def fail(*args):raise RuntimeError('embedding unavailable')
    worker.providers.describe=describe;worker.providers.embed=fail
    tasks.create([ident],True);task=tasks.claim()
    with pytest.raises(RuntimeError):asyncio.run(worker.analyze(task))
    assert db.one('SELECT description FROM media')['description']=='旧描述'
    async def embed(*args):return [1,0,0]
    worker.providers.embed=embed
    asyncio.run(worker.analyze(db.one('SELECT * FROM tasks')))
    assert db.one('SELECT description FROM media')['description'].startswith('新标题')
    assert db.one('SELECT state FROM tasks')['state']=='completed'


def test_frames_run_api_queues_only_videos_without_cloud_confirmation(library):
    db, scanner, source = library
    picture(source / 'photo.jpg')
    picture(source / 'movie.jpg')
    scanner.scan()
    scanner.drain_hashes()
    rows = db.rows('SELECT id,name FROM media ORDER BY name')
    video_id = next(row['id'] for row in rows if row['name'] == 'movie.jpg')
    image_id = next(row['id'] for row in rows if row['name'] == 'photo.jpg')
    db.execute("UPDATE media SET kind='video',analysis_state='done' WHERE id=?", (video_id,))
    db.set_setting('provider', 'codex')
    app = create_app(db.directory, background=False)

    with TestClient(app) as client:
        response = client.post('/api/runs', json={
            'ids': [image_id, video_id],
            'operation': 'frames',
        })
        assert response.status_code == 200
        assert response.json()['count'] == 1
        assert client.post('/api/runs', json={'operation': 'unknown'}).status_code == 422

    run = db.one('SELECT id,kind FROM runs')
    task = db.one('SELECT media_id,config FROM tasks')
    assert run['kind'] == 'frames'
    assert task['media_id'] == video_id
    assert json.loads(task['config'])['task_kind'] == 'frames'


def test_stage_timeout_persists_time_and_pause_does_not_count(library):
    db,scanner,source=library
    picture(source/'a.jpg');scanner.scan();scanner.drain_hashes()
    db.execute("UPDATE media SET kind='video'")
    ident=db.one('SELECT id FROM media')['id']
    tasks=Tasks(db);worker=Worker(db,tasks)
    db.set_setting('video_budget',1)
    tasks.create([ident]);task=tasks.claim()
    async def slow():await asyncio.sleep(5)
    with pytest.raises(Paused):asyncio.run(worker.stage(task,{},slow))
    elapsed=db.one('SELECT elapsed FROM tasks')['elapsed']
    assert 0.9<=elapsed<2
    time.sleep(.1)
    db.restart_recovery()
    assert db.one('SELECT elapsed FROM tasks')['elapsed']==elapsed


def test_video_detail_keeps_history_but_shows_latest_frame_generation(tmp_path):
    app = create_app(tmp_path / 'db', background=False)
    db = app.state.db
    db.execute("INSERT INTO roots(id,path) VALUES ('r','root')")
    db.execute("INSERT INTO media(id,root_id,path,parent,name,kind,updated) VALUES ('m','r','movie','root','movie.mp4','video',0)")
    for task_id, created, stamps in [('old', 1, [0, 5]), ('new', 2, [0, 3, 6])]:
        for index, stamp in enumerate(stamps):
            path = tmp_path / f'{task_id}-{index}.jpg'
            picture(path)
            db.execute(
                "INSERT INTO video_frames(media_id,task_id,kind,time,path,created) VALUES ('m',?,'scene',?,?,?)",
                (task_id, stamp, str(path), created),
            )
    with TestClient(app) as client:
        detail = client.get('/api/media/m').json()
    assert [frame['time'] for frame in detail['frames']] == [0, 3, 6]
    assert detail['frame_generations'] == {'count': 2, 'retained': 5}
    assert db.one('SELECT COUNT(*) AS count FROM video_frames')['count'] == 5
