import os
import time
from PIL import Image
from localmedia.db import Database
from localmedia.scanner import Scanner
from localmedia.media import normalized, file_hash


def test_new_file_is_previewed_before_background_hash(tmp_path, monkeypatch):
    source=tmp_path/'photos'
    source.mkdir()
    path=source/'first.jpg'
    Image.new('RGB',(8,8)).save(path)
    os.utime(path,(time.time()-10,time.time()-10))
    db=Database(tmp_path/'cache')
    db.execute('INSERT INTO roots(id,path) VALUES (?,?)',('r',normalized(source)))
    scanner=Scanner(db)
    hashes=[]
    def digest(path, cancelled=None):
        row=db.one('SELECT * FROM media WHERE path=?',(normalized(path),))
        assert row is not None and row['preview_state']=='ready'
        hashes.append(True)
        return file_hash(path)
    monkeypatch.setattr('localmedia.scanner.file_hash',digest)
    scanner.scan()
    row=db.one('SELECT * FROM media')
    assert row['hash'] is None
    assert not hashes
    scanner.preview(row)
    assert db.one('SELECT * FROM media')['preview_state']=='ready'
    scanner.drain_hashes()
    assert hashes and db.one('SELECT hash FROM media')['hash']
    assert db.one('SELECT identity_pending FROM media')['identity_pending']==0
    assert db.one('SELECT * FROM media')['analysis_state']!='stale'
    assert scanner.preview_wake.is_set()


def test_unchanged_file_keeps_hash_without_rehash(tmp_path, monkeypatch):
    source=tmp_path/'photos'
    source.mkdir()
    path=source/'first.jpg'
    Image.new('RGB',(8,8)).save(path)
    os.utime(path,(time.time()-10,time.time()-10))
    db=Database(tmp_path/'cache')
    db.execute('INSERT INTO roots(id,path) VALUES (?,?)',('r',normalized(source)))
    scanner=Scanner(db)
    scanner.scan();scanner.drain_hashes()
    digest=db.one('SELECT hash FROM media')['hash']
    def unexpected(*args, **kwargs):
        raise AssertionError('unchanged files must not be hashed again')
    monkeypatch.setattr('localmedia.scanner.file_hash',unexpected)
    scanner.scan()
    assert db.one('SELECT hash FROM media')['hash']==digest


def test_changed_file_returns_immediately_to_preview_and_hash_queues(tmp_path):
    source=tmp_path/'photos'
    source.mkdir()
    path=source/'first.bmp'
    Image.new('RGB',(8,8),'red').save(path)
    os.utime(path,(time.time()-10,time.time()-10))
    db=Database(tmp_path/'cache')
    db.execute('INSERT INTO roots(id,path) VALUES (?,?)',('r',normalized(source)))
    scanner=Scanner(db)
    scanner.scan();scanner.drain_hashes()
    old_hash=db.one('SELECT hash FROM media')['hash']
    Image.new('RGB',(9,8),'blue').save(path)
    os.utime(path,(time.time()-10,time.time()-10))
    scanner.scan()
    row=db.one('SELECT * FROM media')
    assert row['hash'] is None and row['preview_state']=='pending'
    scanner.preview(row)
    assert db.one('SELECT preview_state FROM media')['preview_state']=='ready'
    scanner.drain_hashes()
    assert db.one('SELECT hash FROM media')['hash'] != old_hash


def test_ai_task_waits_for_background_hash(tmp_path):
    from localmedia.tasks import Tasks
    source=tmp_path/'photos'
    source.mkdir()
    path=source/'first.jpg'
    Image.new('RGB',(8,8)).save(path)
    os.utime(path,(time.time()-10,time.time()-10))
    db=Database(tmp_path/'cache')
    db.execute('INSERT INTO roots(id,path) VALUES (?,?)',('r',normalized(source)))
    scanner=Scanner(db)
    scanner.scan()
    media_id=db.one('SELECT id FROM media')['id']
    tasks=Tasks(db)
    tasks.create([media_id])
    assert tasks.claim() is None
    scanner.drain_hashes()
    task=tasks.claim()
    assert task is not None and task['source_hash']==db.one('SELECT hash FROM media')['hash']
