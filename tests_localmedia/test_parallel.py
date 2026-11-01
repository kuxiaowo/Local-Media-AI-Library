import asyncio
from types import SimpleNamespace
import pytest
from localmedia.providers import Codex


def test_parallel_codex_routes_interleaved_events():
    async def scenario():
        codex=Codex()
        stream=asyncio.StreamReader()
        codex.proc=SimpleNamespace(stdout=stream)
        reader=asyncio.create_task(codex.read())
        started=[]
        unsubscribed=[]
        thread_starts=0
        max_thread_starts=0
        async def rpc(method,params,timeout=None):
            nonlocal thread_starts,max_thread_starts
            if method=='thread/start':
                thread_starts+=1
                max_thread_starts=max(max_thread_starts,thread_starts)
                try:
                    await asyncio.sleep(.01)
                    return {'thread':{'id':params['model']}}
                finally:
                    thread_starts-=1
            if method=='thread/unsubscribe':
                unsubscribed.append(params['threadId'])
                return {'status':'unsubscribed'}
            started.append(params['threadId'])
            if len(started)==2:
                import json
                for ident in reversed(started):
                    for event in [
                        {'method':'item/completed','params':{'threadId':ident,'item':{'type':'agentMessage','text':ident}}},
                        {'method':'turn/completed','params':{'threadId':ident,'turn':{'status':'completed'}}}
                    ]:
                        stream.feed_data((json.dumps(event)+'\n').encode())
            return {'turn':{'id':params['threadId']}}
        codex.rpc=rpc
        result=await asyncio.wait_for(asyncio.gather(codex.describe('one','',[]),codex.describe('two','',[])),2)
        assert result==['one','two']
        assert max_thread_starts==1
        assert set(unsubscribed)=={'one','two'}
        assert not codex.queues
        stream.feed_eof()
        await reader
    asyncio.run(scenario())


def test_unsubscribe_failure_does_not_hide_completed_description():
    async def scenario():
        codex=Codex()
        async def rpc(method,params,timeout=None):
            if method=='thread/start':
                return {'thread':{'id':'thread'}}
            if method=='turn/start':
                await codex.queues['thread'].put({'method':'item/completed','params':{
                    'threadId':'thread','item':{'type':'agentMessage','text':'描述'}}})
                await codex.queues['thread'].put({'method':'turn/completed','params':{
                    'threadId':'thread','turn':{'status':'completed'}}})
                return {'turn':{'id':'turn'}}
            raise RuntimeError('cleanup failed')
        codex.rpc=rpc
        assert await codex.describe('model','prompt',[])=='描述'
        assert not codex.queues
    asyncio.run(scenario())


def test_sixteen_codex_descriptions_serialize_only_thread_creation():
    async def scenario():
        codex=Codex()
        start_active=0
        max_start_active=0
        turn_active=0
        max_turn_active=0
        turns=[]
        release=asyncio.Event()

        async def rpc(method,params,timeout=None):
            nonlocal start_active,max_start_active,turn_active,max_turn_active
            if method=='thread/start':
                start_active+=1
                max_start_active=max(max_start_active,start_active)
                try:
                    await asyncio.sleep(.005)
                    return {'thread':{'id':params['model']}}
                finally:
                    start_active-=1
            if method=='turn/start':
                ident=params['threadId']
                turns.append(ident)
                turn_active+=1
                max_turn_active=max(max_turn_active,turn_active)
                if len(turns)==16:
                    release.set()
                    for target in turns:
                        await codex.queues[target].put({'method':'item/completed','params':{
                            'threadId':target,'item':{'type':'agentMessage','text':target}}})
                        await codex.queues[target].put({'method':'turn/completed','params':{
                            'threadId':target,'turn':{'status':'completed'}}})
                await release.wait()
                turn_active-=1
                return {'turn':{'id':ident}}
            if method=='thread/unsubscribe':
                return {'status':'unsubscribed'}
            raise AssertionError(method)

        codex.rpc=rpc
        models=[f'model-{number}' for number in range(16)]
        results=await asyncio.wait_for(asyncio.gather(*[
            codex.describe(model,'prompt',[]) for model in models
        ]),2)
        assert results==models
        assert max_start_active==1
        assert max_turn_active==16
        assert not codex.queues

    asyncio.run(scenario())


def test_images_keep_their_limit_while_videos_have_no_outer_limit(tmp_path):
    from localmedia.db import Database
    from localmedia.worker import Worker
    async def scenario():
        db=Database(tmp_path)
        db.set_setting('image_concurrency',2)
        pending={
            'image':[{'id':f'i{i}','media_id':f'i{i}','run_id':'r','media_kind':'image'} for i in range(3)],
            'video':[{'id':f'v{i}','media_id':f'v{i}','run_id':'r','media_kind':'video'} for i in range(4)],
        }
        tasks=SimpleNamespace(
            queued_kinds=lambda:{kind for kind,items in pending.items() if items},
            claim=lambda kind:pending[kind].pop(0) if pending[kind] else None,
        )
        worker=Worker(db,tasks)
        worker.settle=lambda:None
        worker.finish_rebuilds=lambda:None
        release={'image':asyncio.Event(),'video':asyncio.Event()}
        async def analyze(task):
            await release[task['media_kind']].wait()
        worker.analyze=analyze
        loop=asyncio.create_task(worker.loop())
        await asyncio.sleep(.15)
        assert {t['_phase'] for t in worker.active.values()}=={'image','video'}
        assert sum(t['_phase']=='image' for t in worker.active.values())==2
        assert sum(t['_phase']=='video' for t in worker.active.values())==4
        assert len(pending['image'])==1 and not pending['video']
        await worker.cancel(media_ids={'i0'})
        assert 'i0' not in {t['media_id'] for t in worker.active.values()}
        await asyncio.sleep(.15)
        assert {'i1','i2'}.issubset({t['media_id'] for t in worker.active.values()})
        release['image'].set()
        release['video'].set()
        await asyncio.sleep(.2)
        assert not pending['video']
        await worker.close()
        await loop
        assert not worker.active
    asyncio.run(scenario())


def test_concurrency_has_no_configured_upper_limit(tmp_path):
    from fastapi.testclient import TestClient
    from localmedia.api import create_app
    with TestClient(create_app(tmp_path,background=False)) as client:
        for key in ['image_concurrency','cpu_decode_concurrency','intel_decode_concurrency','nvidia_decode_concurrency','ai_request_concurrency','embedding_request_concurrency']:
            for value in [9,100,10000]:
                response=client.patch('/api/settings',json={key:value})
                assert response.status_code==200
                assert response.json()[key]==value
            for value in [0,-1,1.5,True]:
                assert client.patch('/api/settings',json={key:value}).status_code==400


def test_cpu_core_limit_setting_validation(tmp_path):
    import os
    from fastapi.testclient import TestClient
    from localmedia.api import create_app
    total = os.cpu_count() or 1
    with TestClient(create_app(tmp_path, background=False)) as client:
        for value in [0, 1, total]:
            response = client.patch('/api/settings', json={'max_cpu_cores': value})
            assert response.status_code == 200
            assert response.json()['max_cpu_cores'] == value
        for value in [-1, total + 1, 1.5, True]:
            assert client.patch('/api/settings', json={'max_cpu_cores': value}).status_code == 400


def test_decode_and_ai_resource_concurrency_are_independent(tmp_path):
    from localmedia.db import Database
    from localmedia.worker import Worker

    async def scenario():
        db = Database(tmp_path)
        db.set_setting('cpu_decode_concurrency', 2)
        db.set_setting('intel_decode_concurrency', 4)
        db.set_setting('ai_request_concurrency', 3)
        db.set_setting('embedding_request_concurrency', 1)
        worker = Worker(db, SimpleNamespace())
        active = {'cpu_decode_concurrency': 0, 'intel_decode_concurrency': 0,
                  'ai_request_concurrency': 0, 'embedding_request_concurrency': 0}
        maximum = dict(active)
        overlapped = asyncio.Event()

        async def hold(key):
            async with worker.resource_slot(key):
                active[key] += 1
                maximum[key] = max(maximum[key], active[key])
                if all(active.values()):
                    overlapped.set()
                await asyncio.sleep(.05)
                active[key] -= 1

        await asyncio.gather(
            *(hold('cpu_decode_concurrency') for _ in range(6)),
            *(hold('intel_decode_concurrency') for _ in range(8)),
            *(hold('ai_request_concurrency') for _ in range(6)),
            *(hold('embedding_request_concurrency') for _ in range(4)),
        )
        assert overlapped.is_set()
        assert maximum == {'cpu_decode_concurrency': 2, 'intel_decode_concurrency': 4,
                           'ai_request_concurrency': 3, 'embedding_request_concurrency': 1}

    asyncio.run(scenario())


def test_cpu_affinity_mask_selects_requested_number_of_available_cores():
    from localmedia.worker_service import _limited_mask
    available = 0b10110100
    assert _limited_mask(available, 0) == (available, 4, 4)
    assert _limited_mask(available, 2) == (0b00010100, 4, 2)
    with pytest.raises(ValueError):
        _limited_mask(available, 5)


def test_unready_image_does_not_block_video_pipeline(tmp_path):
    from localmedia.db import Database
    from localmedia.worker import Worker
    async def scenario():
        db=Database(tmp_path)
        ready={'image':False}
        pending={
            'image':[{'id':'i','media_id':'i','run_id':'r','media_kind':'image'}],
            'video':[{'id':'v','media_id':'v','run_id':'r','media_kind':'video'}],
        }
        def claim(kind):
            if kind=='image' and not ready['image']:
                return None
            return pending[kind].pop(0) if pending[kind] else None
        tasks=SimpleNamespace(
            queued_kinds=lambda:{kind for kind,items in pending.items() if items},
            claim=claim,
        )
        worker=Worker(db,tasks)
        worker.settle=lambda:None
        worker.finish_rebuilds=lambda:None
        release=asyncio.Event()
        worker.analyze=lambda task:release.wait()
        loop=asyncio.create_task(worker.loop())
        await asyncio.sleep(.15)
        assert {item['_phase'] for item in worker.active.values()}=={'video'}
        assert not pending['video']
        ready['image']=True
        await asyncio.sleep(.15)
        assert {item['_phase'] for item in worker.active.values()}=={'image','video'}
        await worker.close()
        await loop
    asyncio.run(scenario())


def test_legacy_concurrency_migrates_to_images_only(tmp_path):
    import sqlite3
    from localmedia.db import Database
    path=tmp_path/'library.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        conn.execute("INSERT INTO settings VALUES ('ai_concurrency','7')")
    settings=Database(tmp_path).settings()
    assert settings['image_concurrency']==7
    assert 'video_concurrency' not in settings
    assert 'ai_concurrency' not in settings


def test_legacy_total_decode_limit_migrates_to_each_device(tmp_path):
    import sqlite3
    from localmedia.db import Database
    path=tmp_path/'library.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        conn.execute("INSERT INTO settings VALUES ('video_decode_concurrency','7')")
        conn.execute("INSERT INTO settings VALUES ('video_concurrency','9')")
    settings=Database(tmp_path).settings()
    assert settings['cpu_decode_concurrency']==7
    assert settings['intel_decode_concurrency']==7
    assert settings['nvidia_decode_concurrency']==7
    assert 'video_decode_concurrency' not in settings
    assert 'video_concurrency' not in settings
