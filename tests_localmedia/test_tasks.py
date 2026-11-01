import json
from localmedia.db import Database
from localmedia.tasks import Tasks, Paused
import pytest


@pytest.fixture
def setup(tmp_path):
    db = Database(tmp_path)
    db.execute("INSERT INTO roots(id,path) VALUES ('r','root')")
    db.execute("INSERT INTO media(id,root_id,path,parent,name,kind,hash,updated) VALUES ('m','r','p','root','p','video','hash',0)")
    return db, Tasks(db)


def test_pause_resume_preserves_checkpoint(setup):
    db, tasks = setup
    run = tasks.create(['m'])
    task = tasks.claim()
    tasks.pause(run)
    tasks.checkpoint(task['id'], {'batch': 3}, 15)
    with pytest.raises(Paused):
        tasks.boundary(task['id'])
    tasks.resume(run)
    restored = tasks.claim()
    assert json.loads(restored['checkpoint']) == {'batch': 3}
    assert restored['elapsed'] == 15


def test_resume_and_claim_clear_historical_error(setup):
    db, tasks = setup
    run = tasks.create(['m'])
    task = tasks.claim()
    db.execute("UPDATE tasks SET state='paused',error='old failure' WHERE id=?", (task['id'],))
    tasks.resume(run)
    restored = tasks.claim()
    assert restored['id'] == task['id']
    assert db.one('SELECT error FROM tasks WHERE id=?', (task['id'],))['error'] is None


def test_budget_requires_increase(setup):
    db, tasks = setup
    run = tasks.create(['m'])
    task = tasks.claim()
    tasks.checkpoint(task['id'], {}, 1800)
    with pytest.raises(Paused):
        tasks.boundary(task['id'])
    tasks.resume(run)
    assert tasks.claim() is None
    db.set_setting('video_budget', 3600)
    tasks.resume(run)
    assert tasks.claim()['elapsed'] == 1800


def test_restart_never_dispatches(setup):
    db, tasks = setup
    tasks.create(['m'])
    db.restart_recovery()
    assert tasks.claim() is None


def test_snapshot_does_not_contain_keys(setup):
    db, tasks = setup
    db.set_setting('api_key', 'secret')
    tasks.create(['m'])
    assert 'secret' not in tasks.claim()['config']


def test_background_chain_order_is_frozen_and_excludes_siblings(tmp_path):
    db=Database(tmp_path/'cache')
    parent=tmp_path/'photos'
    child=parent/'trip'
    for ident,path,text in [('child',child,'第二天：湖边'),('sibling',parent/'other','不应传入'),('top',parent,'家庭旅行')]:
        db.execute('INSERT INTO roots(id,path,background) VALUES (?,?,?)',(ident,str(path),text))
    db.execute('INSERT INTO media(id,root_id,path,parent,name,kind,note,updated) VALUES (?,?,?,?,?,?,?,0)',
               ('m','child',str(child/'a.jpg'),str(child),'a.jpg','image','这张是第一天'))
    Tasks(db).create(['m'])
    db.execute("UPDATE roots SET background='后来修改'")
    db.execute("UPDATE media SET note='后来修改'")
    config=json.loads(db.one('SELECT config FROM tasks')['config'])
    assert config['background_chain']==[
        {'directory':str(parent),'background':'家庭旅行'},
        {'directory':str(child),'background':'第二天：湖边'}]
    assert config['note']=='这张是第一天'
    assert config['original_filename']=='a.jpg'
    assert config['source_directory']==str(child)
