import asyncio
import base64
import json
from pathlib import Path

import pytest
from PIL import Image

from localmedia.db import Database
from localmedia.providers import Providers, ProviderError


def test_codex_command_auto_recovers_only_official_version_paths(tmp_path, monkeypatch):
    from localmedia.providers import resolve_codex_command
    root = tmp_path / 'OpenAI' / 'Codex' / 'bin'
    old = root / 'old-version' / 'codex.exe'
    current = root / 'current-version' / 'codex.exe'
    current.parent.mkdir(parents=True)
    current.write_bytes(b'current')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    assert resolve_codex_command(str(old)) == str(current.resolve())
    with pytest.raises(ProviderError, match='不存在'):
        resolve_codex_command(str(tmp_path / 'custom' / 'codex.exe'))


def test_provider_rebuilds_idle_codex_after_command_change(tmp_path):
    db = Database(tmp_path)
    db.set_setting('codex_command', 'first')
    service = Providers(db)
    closed = []

    class StubCodex:
        command = 'first'
        queues = {}
        pending = {}
        rpc_timeout = 0

        async def close(self):
            closed.append(True)

    service.codex = StubCodex()
    db.set_setting('codex_command', 'second')

    async def scenario():
        with pytest.raises(ProviderError):
            await service.get_codex()
        assert closed == [True]

    asyncio.run(scenario())


def test_codex_reloads_changed_auth_only_when_idle(tmp_path, monkeypatch):
    from localmedia.providers import Codex
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    auth = tmp_path / 'auth.json'
    auth.write_text('first', encoding='utf-8')
    codex = Codex()
    codex.auth_signature = codex.current_auth_signature()
    class Process:
        returncode = None
    codex.proc = Process()
    closed = []
    async def close():
        closed.append(True)
        codex.proc = None
    async def spawn(*args, **kwargs):
        raise RuntimeError('spawn reached')
    codex.close = close
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    async def scenario():
        await codex.start()
        assert not closed
        auth.write_text('updated-account', encoding='utf-8')
        codex.queues['active'] = asyncio.Queue()
        await codex.start()
        assert not closed
        codex.queues.clear()
        codex.pending[1] = asyncio.get_running_loop().create_future()
        await codex.start()
        assert not closed
        codex.pending.clear()
        with pytest.raises(RuntimeError, match='spawn reached'):
            await codex.start()
        assert closed == [True]
        assert codex.auth_signature == codex.current_auth_signature()
    asyncio.run(scenario())


def test_codex_effort_wire_and_task_snapshot(tmp_path):
    from localmedia.providers import Codex
    from localmedia.tasks import Tasks
    db=Database(tmp_path)
    db.set_setting('codex_effort','high')
    db.set_setting('codex_summary_effort','low')
    db.set_setting('request_timeout',240)
    db.execute("INSERT INTO roots(id,path) VALUES ('r','root')")
    db.execute("INSERT INTO media(id,root_id,path,parent,name,kind,updated) VALUES ('m','r','p','root','p','image',0)")
    Tasks(db).create(['m'])
    config=json.loads(db.rows('SELECT config FROM tasks')[0]['config'])
    db.set_setting('codex_effort','medium')
    db.set_setting('request_timeout',45)
    assert config['codex_effort']=='high'
    assert config['request_timeout']==240
    service=Providers(db)
    config.update(provider='codex',model='visual',summary_model='text')
    async def scenario():
        codex=Codex(cwd=str(tmp_path))
        calls=[]
        async def rpc(method,params,timeout=None):
            calls.append((method,params,timeout))
            if method=='thread/start':return {'thread':{'id':'thread'}}
            if method=='thread/unsubscribe':return {'status':'unsubscribed'}
            await codex.queues["thread"].put({'method':'item/completed','params':{'threadId':'thread','item':{'type':'agentMessage','text':'描述'}}})
            await codex.queues["thread"].put({'method':'turn/completed','params':{'threadId':'thread','turn':{'status':'completed'}}})
            return {'turn':{'id':'turn'}}
        codex.rpc=rpc
        async def get():return codex
        service.get_codex=get
        assert await service.describe(config,'test')=='描述'
        assert await service.describe(config,'test',summary=True)=='描述'
        config['codex_effort']=''
        await service.describe(config,'test')
        turns=[p for m,p,_ in calls if m=='turn/start']
        assert turns[0]['effort']=='high'
        assert turns[1]['effort']=='low'
        assert 'effort' not in turns[2]
        assert [p['model'] for m,p,_ in calls if m=='thread/start']==['visual','text','visual']
        assert all(timeout==240 for method,_,timeout in calls if method in {'thread/start','turn/start'})
    asyncio.run(scenario())


def test_codex_rpc_wait_uses_configured_timeout_and_reports_method(monkeypatch):
    from localmedia.providers import Codex
    codex=Codex(rpc_timeout=73)
    codex.send=lambda payload:None
    async def start():pass
    codex.start=start
    waits=[]
    async def complete(future,timeout):
        waits.append(timeout)
        future.set_result({'ok':True})
        return await future
    monkeypatch.setattr(asyncio,'wait_for',complete)
    async def scenario():
        assert await codex.rpc('account/read')=={'ok':True}
        assert await codex.rpc('thread/start',timeout=240)=={'ok':True}
        assert waits==[73,240]
        assert not codex.pending
        async def expire(future,timeout):raise TimeoutError()
        monkeypatch.setattr(asyncio,'wait_for',expire)
        with pytest.raises(ProviderError,match='Codex RPC turn/start 超时'):
            await codex.rpc('turn/start')
        assert not codex.pending
    asyncio.run(scenario())


def test_existing_codex_uses_current_timeout_for_service_calls(tmp_path):
    from localmedia.providers import Codex
    db=Database(tmp_path)
    service=Providers(db)
    codex=Codex()
    async def start():pass
    codex.start=start
    service.codex=codex
    async def scenario():
        db.set_setting('request_timeout',48)
        assert (await service.get_codex()).rpc_timeout==48
        db.set_setting('request_timeout',96)
        assert (await service.get_codex()).rpc_timeout==96
    asyncio.run(scenario())


def test_openai_chat_and_embedding_wire_contract(tmp_path):
    db=Database(tmp_path/'db')
    db.set_setting('api_key','test-private')
    db.set_setting('embedding_key','embed-private')
    config={**db.settings(),'provider':'openai','model':'vision','embedding_provider':'openai','embedding_model':'embed'}
    service=Providers(db)
    path=tmp_path/'image.jpg';Image.new('RGB',(2000,1000),'red').save(path)
    calls=[]
    async def request(url,key,endpoint,payload):
        calls.append((endpoint,key,payload))
        if endpoint=='/embeddings':return {'data':[{'embedding':[1,2]}]}
        return {'choices':[{'message':{'content':'描述'}}]}
    service.request=request
    async def scenario():
        assert await service.describe(config,'背景',[path])=='描述'
        assert await service.embed(config,'描述')==[1,2]
    asyncio.run(scenario())
    assert calls[0][0:2]==('/chat/completions','test-private')
    assert calls[0][2]['messages'][0]['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,')
    prompt = calls[0][2]['messages'][0]['content'][0]['text']
    assert '原始文件名和文件所在目录' in prompt
    assert '完整连贯的叙事' in prompt
    assert '不确定' not in prompt
    assert calls[1][2]=={'model':'embed','input':'描述'}


def test_openai_visual_references_keep_original_png_payload(tmp_path):
    db = Database(tmp_path / 'db')
    db.set_setting('api_key', 'test-private')
    config = {**db.settings(), 'provider': 'openai', 'model': 'vision',
              'visual_reference_count': 1}
    service = Providers(db)
    reference = tmp_path / 'reference.png'
    media = tmp_path / 'media.jpg'
    Image.new('RGB', (1600, 800), 'blue').save(reference)
    Image.new('RGB', (1600, 800), 'red').save(media)
    original = base64.b64encode(reference.read_bytes()).decode()
    payloads = []

    async def request(url, key, endpoint, payload):
        payloads.append(payload)
        return {'choices': [{'message': {'content': '描述'}}]}

    service.request = request
    asyncio.run(service.describe(config, '识别', [reference, media]))
    content = payloads[0]['messages'][0]['content']
    assert content[1]['image_url']['url'] == 'data:image/png;base64,' + original
    assert content[2]['image_url']['url'].startswith('data:image/jpeg;base64,')


def test_rebuild_edit_adds_latest_revision_to_both_spaces(tmp_path):
    from fastapi.testclient import TestClient
    from localmedia.api import create_app
    from localmedia.db import profile
    app=create_app(tmp_path,background=False)
    db=app.state.db
    db.execute("INSERT INTO roots(id,path) VALUES ('r','root')")
    db.execute("INSERT INTO media(id,root_id,path,parent,name,kind,updated) VALUES ('m','r','p','root','p','image',0)")
    old={**db.settings(),'embedding_model':'old'}
    new={**db.settings(),'embedding_model':'new'}
    db.set_setting('active_profile',profile(old))
    db.execute('INSERT INTO rebuilds VALUES (?,?,?)',(profile(new),json.dumps(new),'running'))
    with TestClient(app) as client:
        client.patch('/api/media/m/description',json={'text':'更新的描述'})
    jobs=db.rows('SELECT profile,revision FROM vector_jobs')
    assert {j['profile'] for j in jobs}=={profile(old),profile(new)}
    assert all(j['revision']==1 for j in jobs)


def test_changing_endpoint_never_sends_old_key_to_new_host(tmp_path):
    from fastapi.testclient import TestClient
    from localmedia.api import create_app
    app=create_app(tmp_path,background=False)
    with TestClient(app) as client:
        client.patch('/api/settings',json={'base_url':'https://old.example/v1','api_key':'old-secret'})
        response=client.patch('/api/settings',json={'base_url':'https://new.example/v1'})
        assert not response.json()['api_key_configured']
        assert app.state.db.credential('description','https://old.example/v1')=='old-secret'
        assert app.state.db.credential('description','https://new.example/v1')==''


def test_ollama_truncated_output_is_never_published(tmp_path):
    db=Database(tmp_path);service=Providers(db)
    config={**db.settings(),'model':'vision'}
    async def request(*args):
        return {'done_reason':'length','message':{'content':'partial'}}
    service.request=request
    with pytest.raises(ProviderError,match='长度限制'):
        asyncio.run(service.describe(config,'test'))


def test_library_prevents_duplicate_runtime(tmp_path):
    from localmedia.instance import library_lock
    with library_lock(tmp_path):
        with pytest.raises(RuntimeError,match='另一个'):
            with library_lock(tmp_path):
                pass
    with library_lock(tmp_path):
        pass
