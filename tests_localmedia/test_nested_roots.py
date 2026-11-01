import os
import time
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from localmedia.api import create_app
from localmedia.scanner import Scanner


@pytest.mark.parametrize('child_first',[True,False])
def test_nested_roots_preserve_media_and_include_descendants(tmp_path, child_first):
    parent=tmp_path/'photos'
    child=parent/'trip'
    child.mkdir(parents=True)
    for path in [parent/'a.jpg',child/'b.jpg']:
        Image.new('RGB',(8,8)).save(path)
        os.utime(path,(time.time()-10,time.time()-10))
    app=create_app(tmp_path/'cache',background=False)
    db=app.state.db
    scanner=Scanner(db)
    with TestClient(app) as client:
        first,second=(child,parent) if child_first else (parent,child)
        one=client.post('/api/roots',json={'path':str(first),'background':'original'}).json()['id']
        scanner.scan()
        before=db.rows('SELECT id,path FROM media')
        db.execute("UPDATE media SET description='保留描述',note='保留备注',analysis_state='done'")
        response=client.post('/api/roots',json={'path':str(second),'background':'new'})
        assert response.status_code==200
        assert response.json()['path']==os.path.normcase(os.path.normpath(str(second.resolve())))
        two=response.json()['id']
        scanner.scan();scanner.scan()
        assert len(db.rows('SELECT id FROM media'))==2
        for row in before:
            kept=db.one('SELECT * FROM media WHERE id=?',(row['id'],))
            assert kept['path']==row['path']
            assert kept['description']=='保留描述' and kept['note']=='保留备注'
            assert kept['availability']=='online'
        parent_id,child_id=(two,one) if child_first else (one,two)
        assert client.get('/api/media',params={'root_id':parent_id}).json()['total']==2
        assert client.get('/api/media',params={'root_id':child_id}).json()['total']==1
        rows=client.get('/api/roots').json()
        assert sum(r['count'] for r in rows)==2
        assert next(r for r in rows if r['id']==parent_id)['total_count']==2
        assert next(r for r in rows if r['id']==child_id)['parent_id']==parent_id
        tree=client.get('/api/folder-tree').json()
        assert len(tree)==2
        top=next(n for n in tree if n['parent_path'] is None)
        sub=next(n for n in tree if n['parent_path'] is not None)
        assert top['count']==2 and sub['count']==1
        assert sub['parent_path']==top['path'] and sub['root_id']==child_id
        assert client.post('/api/roots',json={'path':str(parent)}).status_code==400


def test_hierarchy_reparents_on_insert_and_relocation(tmp_path):
    parent=tmp_path/'photos'
    middle=parent/'year'
    leaf=middle/'trip'
    leaf.mkdir(parents=True)
    outside=tmp_path/'outside'
    outside.mkdir()
    app=create_app(tmp_path/'cache',background=False)
    with TestClient(app) as client:
        def add(path):
            return client.post('/api/roots',json={'path':str(path)}).json()['id']
        leaf_id=add(leaf)
        parent_id=add(parent)
        tree=client.get('/api/folder-tree').json()
        assert len(tree)==3
        assert sum(n['parent_path'] is None for n in tree)==1
        def relations():return {r['id']:r['parent_id'] for r in client.get('/api/roots').json()}
        assert relations()[leaf_id]==parent_id
        middle_id=add(middle)
        assert relations()=={leaf_id:middle_id,middle_id:parent_id,parent_id:None}
        assert client.patch('/api/roots/'+middle_id+'/location',json={'path':str(outside)}).status_code==200
        assert relations()=={leaf_id:parent_id,middle_id:None,parent_id:None}


def test_visual_enhancement_is_an_independent_directory_setting(tmp_path):
    source = tmp_path / 'photos'
    source.mkdir()
    app = create_app(tmp_path / 'cache', background=False)
    with TestClient(app) as client:
        ident = client.post('/api/roots', json={'path': str(source)}).json()['id']
        root = next(row for row in client.get('/api/roots').json() if row['id'] == ident)
        assert root['visual_enhancement'] is False

        response = client.patch(
            f'/api/roots/{ident}/visual-enhancement',
            json={'enabled': True},
        )
        assert response.status_code == 200
        assert response.json()['visual_enhancement'] is True
        root = next(row for row in client.get('/api/roots').json() if row['id'] == ident)
        assert root['visual_enhancement'] is True
