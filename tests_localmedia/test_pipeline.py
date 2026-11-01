import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from localmedia.db import Database, profile
from localmedia.index import enqueue, normalize
from localmedia.media import file_hash
from localmedia.tasks import Tasks, Paused
from localmedia.worker import Worker


@pytest.fixture
def video(tmp_path):
    db=Database(tmp_path/'db')
    source=tmp_path/'movie.mp4';source.write_bytes(b'media-content')
    db.execute("INSERT INTO roots(id,path) VALUES ('r','root')")
    db.execute("INSERT INTO media(id,root_id,path,parent,name,kind,hash,preview_state,preview,metadata,description,updated) VALUES ('m','r',?,'root','movie.mp4','video',?,'ready',?,'{}','旧描述',0)", (str(source),file_hash(source),str(source)))
    tasks=Tasks(db);worker=Worker(db,tasks)
    async def frames(task,cp,media,config):
        return [{'time':i*5,'path':str(source)} for i in range(13)]
    async def embed(config,text):return [1,2,3]
    worker.frames=frames;worker.providers.embed=embed
    return db,tasks,worker


def test_pause_at_batch_boundary_and_resume_no_repetition(video):
    db,tasks,worker=video
    run=tasks.create(['m']);task=tasks.claim();calls=[]
    async def describe(config,prompt,images=(),summary=False):
        calls.append(prompt)
        if len(calls)==1:tasks.pause(run)
        return f'事件{len(calls)}'
    worker.providers.describe=describe
    with pytest.raises(Paused):asyncio.run(worker.analyze(task))
    cp=json.loads(db.one('SELECT checkpoint FROM tasks')['checkpoint'])
    assert len(cp['batches'])==1 and cp['next_frame']==5
    assert db.one('SELECT description FROM media')['description']=='旧描述'
    tasks.resume(run)
    asyncio.run(worker.analyze(tasks.claim()))
    assert len(calls)==4  # 3 overlapping batches + final summary
    assert '事件1' in calls[1]
    assert '[25, 25]' in calls[1]  # overlap timestamp
    assert '"原始文件名": "movie.mp4"' in calls[0]
    assert '"文件所在目录": "root"' in calls[0]
    assert '标题必须原样沿用该名称' in calls[-1]
    assert '不确定' not in calls[-1]
    assert db.one('SELECT state FROM tasks')['state']=='completed'


def test_immediate_stop_keeps_completed_batches(video):
    db,tasks,worker=video
    run=tasks.create(['m']);task=tasks.claim();calls=0
    async def scenario():
        started=asyncio.Event()
        async def describe(config,prompt,images=(),summary=False):
            nonlocal calls
            calls+=1
            if calls==2:
                started.set();await asyncio.sleep(30)
            return '已完成事件'
        worker.providers.describe=describe
        running=asyncio.create_task(worker.analyze(task))
        await started.wait()
        tasks.pause(run,immediate=True);running.cancel()
        await asyncio.gather(running,return_exceptions=True)
        cp=json.loads(db.one('SELECT checkpoint FROM tasks')['checkpoint'])
        assert len(cp['batches'])==1 and cp['next_frame']==5
        assert db.one('SELECT description FROM media')['description']=='旧描述'
    asyncio.run(scenario())


def test_video_summary_has_no_phantom_reference_images_and_preserves_categories(video):
    db, tasks, worker = video
    db.execute("UPDATE roots SET visual_enhancement=1")
    pack = db.directory / 'visual-reference-pack'
    pack.mkdir()
    (pack / 'reference.png').write_bytes(b'reference bytes')
    calls = []

    async def describe(config, prompt, images=(), summary=False):
        calls.append((prompt, list(images), summary))
        return '可见道具：眼罩、口球、绳索。'

    worker.providers.describe = describe
    tasks.create(['m'])
    asyncio.run(worker.analyze(tasks.claim()))
    visuals = [row for row in calls if not row[2]]
    final = calls[-1]
    assert all('时间戳只对应参考图之后的目标帧' in row[0] for row in visuals)
    assert all(row[1][0] == str((pack / 'reference.png').resolve()) for row in visuals)
    assert final[1] == [] and final[2] is True
    assert '其后才是待分析媒体' not in final[0]
    assert '已明确识别的道具名称' in final[0]
    assert '不得仅因其他片段没有提及' in final[0]


def test_quota_updates_never_pause_runs(video):
    db,tasks,worker=video
    db.set_setting('provider','codex')
    tasks.create(['m'])
    worker.quota_update({'rateLimits':{'secondary':{'windowDurationMins':300,'usedPercent':90}}})
    assert worker.quota_state['state']=='ready'
    assert db.one('SELECT state FROM runs')['state']=='running'
    worker.quota_update({'rateLimitsByLimitId':{'codex':{'primary':{'windowDurationMins':10080,'usedPercent':100}}}})
    assert worker.quota_state['state']=='ready'
    assert db.one('SELECT state FROM runs')['state']=='running'
    assert tasks.claim() is not None


def test_quota_query_failure_only_marks_status_unknown(video):
    db,tasks,worker=video
    db.set_setting('provider','codex')
    tasks.create(['m'])
    async def failed():raise OSError('not connected')
    worker.providers.get_codex=failed
    asyncio.run(worker.quota_check())
    assert worker.quota_state['state']=='unknown'
    assert tasks.claim() is not None


def test_frames_run_only_extracts_and_preserves_ai_state(video):
    db, tasks, worker = video
    db.set_setting('provider', 'codex')
    db.execute("UPDATE media SET analysis_state='done',vector_state='ready',revision=7 WHERE id='m'")

    async def forbidden(*args, **kwargs):
        raise AssertionError('frames-only task must not call an AI provider or quota endpoint')

    worker.providers.describe = forbidden
    worker.providers.embed = forbidden
    worker.quota_check = forbidden
    run = tasks.create(['m'], run_kind='frames')
    task = tasks.claim('video')

    assert task['run_kind'] == 'frames'
    assert json.loads(task['config'])['task_kind'] == 'frames'
    asyncio.run(worker.execute(task))

    media = db.one("SELECT description,analysis_state,vector_state,revision FROM media WHERE id='m'")
    assert media == {
        'description': '旧描述',
        'analysis_state': 'done',
        'vector_state': 'ready',
        'revision': 7,
    }
    assert db.one('SELECT state FROM tasks WHERE run_id=?', (run,))['state'] == 'completed'
    assert db.one('SELECT state,kind FROM runs WHERE id=?', (run,)) == {
        'state': 'completed', 'kind': 'frames',
    }


def test_visual_reference_pack_is_snapshotted_and_precedes_media(tmp_path):
    db = Database(tmp_path / 'db')
    source = tmp_path / 'photo.jpg'
    Image = pytest.importorskip('PIL.Image')
    Image.new('RGB', (12, 12), 'red').save(source)
    reference_dir = db.directory / 'visual-reference-pack'
    reference_dir.mkdir()
    first = reference_dir / '口罩.png'
    second = reference_dir / '手铐和手部束缚.png'
    Image.new('RGB', (8, 8), 'black').save(first)
    Image.new('RGB', (8, 8), 'gray').save(second)
    db.execute("INSERT INTO roots(id,path,visual_enhancement) VALUES ('r','root',1)")
    db.execute(
        "INSERT INTO media(id,root_id,path,parent,name,kind,hash,preview_state,preview,metadata,updated) "
        "VALUES ('m','r',?,'root','photo.jpg','image',?,'ready',?,'{}',0)",
        (str(source), file_hash(source), str(source)),
    )
    tasks = Tasks(db)
    worker = Worker(db, tasks)
    calls = []

    async def describe(config, prompt, images=(), summary=False):
        calls.append((prompt, list(images)))
        return '标题：测试\n已识别'

    async def embed(config, text):
        return [1, 0, 0]

    worker.providers.describe = describe
    worker.providers.embed = embed
    tasks.create(['m'])
    task = tasks.claim()
    snapshot = json.loads(task['config'])
    assert snapshot['visual_reference_count'] == 2
    assert [row['category'] for row in snapshot['visual_references']] == ['口罩', '手铐和手部束缚']

    asyncio.run(worker.analyze(task))
    prompt, images = calls[0]
    assert images == [str(first.resolve()), str(second.resolve()), str(source)]
    assert '前 2 张是视觉类别参考拼图' in prompt
    assert '直接使用对应类别名' in prompt


def test_rebuild_retains_old_index_until_success(video):
    db,tasks,worker=video
    old=db.settings();old['embedding_model']='old'
    new={**old,'embedding_model':'new'}
    db.set_setting('active_profile',profile(old))
    vector=normalize([1,2,3])
    db.execute('INSERT INTO vectors VALUES (?,?,?,?,?)',('m',profile(old),0,3,vector.tobytes()))
    with db.connect() as conn:
        enqueue(conn,'m',0,new)
        conn.execute('INSERT INTO rebuilds VALUES (?,?,?)',(profile(new),json.dumps(new),'running'))
    assert db.settings()['active_profile']==profile(old)
    asyncio.run(worker.vector_job(db.one('SELECT * FROM vector_jobs')))
    assert db.settings()['active_profile']==profile(new)
    assert len(db.rows('SELECT * FROM vectors'))==2


def test_resume_detects_on_disk_change_before_request(video):
    db,tasks,worker=video
    run=tasks.create(['m']);task=tasks.claim()
    tasks.checkpoint(task['id'],{'batches':[{'description':'checkpoint'}]},5)
    tasks.pause(run)
    db.restart_recovery()
    source=Path(db.one('SELECT path FROM media')['path']);source.write_bytes(b'different')
    tasks.resume(run)
    with pytest.raises(Paused):asyncio.run(worker.analyze(tasks.claim()))
    assert db.one('SELECT reason FROM tasks')['reason']=='source_changed'


def test_old_failed_run_cannot_overwrite_new_completion(video):
    db,tasks,worker=video
    first=tasks.create(['m']);old=tasks.claim()
    db.execute("UPDATE tasks SET state='failed' WHERE id=?",(old['id'],))
    second=tasks.create(['m'],True);new=tasks.claim()
    db.execute("UPDATE tasks SET state='completed',updated=? WHERE id=?",(time.time()+1,new['id']))
    db.execute("UPDATE tasks SET state='paused' WHERE id=?",(old['id'],))
    tasks.resume(first)
    assert tasks.claim() is None
    assert db.one('SELECT error FROM tasks WHERE id=?',(old['id'],))['error']
