import asyncio
import httpx
import time
from localmedia.api import create_app
from localmedia.tasks import Tasks


def test_delete_run_cancels_only_target_preserves_media(tmp_path):
    async def scenario():
        app=create_app(tmp_path,background=False)
        db=app.state.db
        db.execute("INSERT INTO roots(id,path) VALUES ('r','root')")
        for ident in ['one','two']:
            db.execute("INSERT INTO media(id,root_id,path,parent,name,kind,description,preview,updated) VALUES (?,'r',?,'root',?,'image','saved','preview.jpg',0)",(ident,ident,ident))
        one=Tasks(db).create(['one'])
        two=Tasks(db).create(['two'])
        worker=app.state.worker
        started=asyncio.Event()
        cancelled=asyncio.Event()
        async def running():
            started.set()
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        future=asyncio.create_task(running())
        worker.active[future]={'run_id':one,'media_id':'one'}
        await started.wait()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            assert (await client.delete('/api/runs/'+one)).status_code==200
            assert cancelled.is_set()
            assert not db.one('SELECT id FROM runs WHERE id=?',(one,))
            assert not db.rows('SELECT id FROM tasks WHERE run_id=?',(one,))
            assert db.one('SELECT id FROM tasks WHERE run_id=?',(two,))
            assert db.one("SELECT description,preview FROM media WHERE id='one'")=={'description':'saved','preview':'preview.jpg'}
            assert (await client.delete('/api/runs/'+one)).status_code==404
    asyncio.run(scenario())


def test_delete_run_keeps_persisted_video_frames(tmp_path):
    app=create_app(tmp_path,background=False)
    db=app.state.db
    frame=tmp_path/'frames'/'frame.jpg';frame.parent.mkdir();frame.write_bytes(b'jpg')
    db.execute("INSERT INTO roots(id,path) VALUES ('r','root')")
    db.execute("INSERT INTO media(id,root_id,path,parent,name,kind,hash,updated) VALUES ('m','r','movie','root','movie.mp4','video','hash',0)")
    run=Tasks(db).create(['m'])
    task=db.one('SELECT id FROM tasks WHERE run_id=?',(run,))['id']
    db.execute("INSERT INTO video_frames(media_id,task_id,kind,time,path,created) VALUES ('m',?,'scene',1.5,?,?)",(task,str(frame),time.time()))
    # The frame catalog deliberately has no FK to disposable task rows.
    db.execute('DELETE FROM tasks WHERE run_id=?',(run,));db.execute('DELETE FROM runs WHERE id=?',(run,))
    assert db.one("SELECT kind,time FROM video_frames WHERE media_id='m'")=={'kind':'scene','time':1.5}
