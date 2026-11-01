import os
import sqlite3
import time

from fastapi.testclient import TestClient
from PIL import Image

from localmedia.api import create_app
from localmedia.db import Database
from localmedia.media import normalized
from localmedia.scanner import Scanner


def image(path):
    Image.new('RGB', (8, 8)).save(path)
    os.utime(path, (time.time() - 10, time.time() - 10))


def test_existing_database_adds_enabled_column_as_enabled(tmp_path):
    data = tmp_path / 'cache'
    data.mkdir()
    with sqlite3.connect(data / 'library.sqlite3') as conn:
        conn.execute("CREATE TABLE roots (id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, background TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'pending', error TEXT, scanned REAL)")
        conn.execute("INSERT INTO roots(id,path) VALUES ('old','c:\\\\photos')")
    db = Database(data)
    assert db.one("SELECT enabled FROM roots WHERE id='old'")['enabled'] == 1


def test_disabling_parent_hides_nested_roots_and_preserves_records(tmp_path):
    parent = tmp_path / 'photos'
    child = parent / 'trip'
    child.mkdir(parents=True)
    image(parent / 'a.jpg')
    image(child / 'b.jpg')

    app = create_app(tmp_path / 'cache', background=False)
    db = app.state.db
    scanner = Scanner(db)
    with TestClient(app) as client:
        parent_id = client.post('/api/roots', json={'path': str(parent)}).json()['id']
        child_id = client.post('/api/roots', json={'path': str(child)}).json()['id']
        scanner.scan()
        records = db.rows('SELECT id,path FROM media ORDER BY path')
        assert len(records) == 2

        response = client.patch('/api/roots/' + parent_id + '/enabled', json={'enabled': False})
        assert response.status_code == 200
        roots = {row['id']: row for row in client.get('/api/roots').json()}
        assert roots[parent_id]['enabled'] is False
        assert roots[parent_id]['effective_enabled'] is False
        assert roots[child_id]['enabled'] is True
        assert roots[child_id]['effective_enabled'] is False
        assert client.get('/api/media').json()['total'] == 0
        assert client.get('/api/folder-tree').json() == []
        assert db.rows('SELECT id,path FROM media ORDER BY path') == records

        image(child / 'new.jpg')
        scanner.scan()
        assert len(db.rows('SELECT id FROM media')) == 2

        response = client.patch('/api/roots/' + parent_id + '/enabled', json={'enabled': True})
        assert response.status_code == 200
        scanner.scan()
        assert client.get('/api/media').json()['total'] == 3
        assert len(client.get('/api/folder-tree').json()) == 2


def test_disabling_nested_root_only_hides_its_subtree(tmp_path):
    parent = tmp_path / 'photos'
    child = parent / 'trip'
    child.mkdir(parents=True)
    image(parent / 'a.jpg')
    image(child / 'b.jpg')

    app = create_app(tmp_path / 'cache', background=False)
    scanner = Scanner(app.state.db)
    with TestClient(app) as client:
        parent_id = client.post('/api/roots', json={'path': str(parent)}).json()['id']
        child_id = client.post('/api/roots', json={'path': str(child)}).json()['id']
        scanner.scan()

        assert client.patch('/api/roots/' + child_id + '/enabled', json={'enabled': False}).status_code == 200
        assert client.get('/api/media').json()['total'] == 1
        assert client.get('/api/media', params={'root_id': parent_id}).json()['total'] == 1
        tree = client.get('/api/folder-tree').json()
        assert len(tree) == 1
        assert tree[0]['path'] == normalized(parent)
        assert len(app.state.db.rows('SELECT id FROM media')) == 2
