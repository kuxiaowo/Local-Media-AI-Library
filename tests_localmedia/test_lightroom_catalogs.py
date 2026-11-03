from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from localmedia.lightroom_catalogs import CatalogInventory, hidden_path, verify_normal_full, edit_records
from localmedia.lightroom_workflow import restore_normal
from localmedia.lightroom_native import NativeCatalogs


def inventory(photos):
    return CatalogInventory(Path('example.lrcat'), photos, 0, 0, 'test')


def test_hidden_folder_boundary_includes_children_but_not_similar_names():
    roots = ['D:/保密测试']
    assert hidden_path(r'd:\保密测试\child\image.jpg', roots)
    assert hidden_path(r'D:\保密测试', roots)
    assert not hidden_path(r'D:\保密测试备份\image.jpg', roots)
    assert not hidden_path(r'E:\保密测试\image.jpg', roots)


def test_full_contains_public_and_hidden_normal_contains_only_public():
    full = inventory({'public':'d:\\photos\\one.jpg', 'private':'d:\\保密测试\\two.jpg'})
    normal = inventory({'public':'d:\\photos\\one.jpg'})
    assert verify_normal_full(normal, full, ['D:/保密测试']) == {'normal_count':1,'hidden_count':1,'full_count':2}


@pytest.mark.parametrize('photos,error', [
    ({'public':'d:\\photos\\one.jpg','private':'d:\\保密测试\\two.jpg'}, '隐藏'),
    ({}, '范围不一致'),
    ({'public':'d:\\photos\\different.jpg'}, '不同原文件'),
])
def test_invalid_normal_is_rejected(photos, error):
    full = inventory({'public':'d:\\photos\\one.jpg','private':'d:\\保密测试\\two.jpg'})
    with pytest.raises(ValueError, match=error):
        verify_normal_full(inventory(photos), full, ['D:/保密测试'])


def test_publish_retains_previous_catalog_and_sidecar(tmp_path):
    normal = tmp_path/'normal'/'Normal.lrcat'
    export = tmp_path/'exports'/'new'/'Normal.lrcat'
    for p in [normal,export]:p.parent.mkdir(parents=True)
    normal.write_bytes(b'previous catalog');export.write_bytes(b'new catalog')
    Path(str(normal)+'-wal').write_bytes(b'previous WAL')
    Path(str(normal)+'-shm').write_bytes(b'previous shared memory')
    old_data=Path(str(normal)+'-data');old_data.mkdir();(old_data/'mask').write_bytes(b'old mask')
    new_data=Path(str(export)+'-data');new_data.mkdir();(new_data/'mask').write_bytes(b'new mask')
    backup=Path(restore_normal(export,normal,tmp_path))
    assert normal.read_bytes()==b'new catalog'
    assert (old_data/'mask').read_bytes()==b'new mask'
    assert (backup/'Normal.lrcat').read_bytes()==b'previous catalog'
    assert (backup/'Normal.lrcat-data'/'mask').read_bytes()==b'old mask'
    assert (backup/'Normal.lrcat-wal').read_bytes()==b'previous WAL'
    assert not Path(str(normal)+'-wal').exists()
    assert not Path(str(normal)+'-shm').exists()


def test_publish_rejects_running_catalog_before_moving_anything(tmp_path):
    normal=tmp_path/'normal'/'Normal.lrcat';normal.parent.mkdir()
    normal.write_bytes(b'original')
    Path(str(normal)+'.lock').touch()
    with pytest.raises(ValueError):restore_normal(tmp_path/'exports'/'Normal.lrcat',normal,tmp_path)
    assert normal.read_bytes()==b'original'


def test_failed_publication_restores_previous_catalog(tmp_path,monkeypatch):
    normal=tmp_path/'normal'/'Normal.lrcat';normal.parent.mkdir()
    export=tmp_path/'exports'/'Normal.lrcat';export.parent.mkdir()
    normal.write_bytes(b'original');export.write_bytes(b'new')
    rename=Path.rename
    def fail(path,destination):
        if path==export:raise OSError('disk write failed')
        return rename(path,destination)
    monkeypatch.setattr(Path,'rename',fail)
    with pytest.raises(OSError):restore_normal(export,normal,tmp_path)
    assert normal.read_bytes()==b'original'
    assert export.read_bytes()==b'new'


def test_native_merge_snapshot_ids_and_numeric_storage_do_not_hide_content_changes(tmp_path):
    def catalog(name,number,snapshot_id,text):
        path=tmp_path/name
        c=sqlite3.connect(path)
        try:
            c.executescript('''CREATE TABLE Adobe_images(id_local,id_global,rating,pick,colorLabels,orientation);
                INSERT INTO Adobe_images VALUES(1,'photo',5,0,'','AB');
                CREATE TABLE Adobe_libraryImageDevelopHistoryStep(id_local,id_global,image,name,text,hasDevelopAdjustments);
                CREATE TABLE Adobe_libraryImageDevelopSnapshot(id_local,id_global,image,snapshotID,name,text);''')
            c.execute('INSERT INTO Adobe_libraryImageDevelopHistoryStep VALUES(1,?,1,?,?,?)',(name,'Exposure','exposure=1.25',number))
            c.execute('INSERT INTO Adobe_libraryImageDevelopSnapshot VALUES(2,?,1,?,?,?)',(name,snapshot_id,'Snapshot',text))
            c.commit()
        finally:c.close()
        return path
    a=catalog('a.lrcat',1,'old-id','exposure=1.25')
    b=catalog('b.lrcat',1.0,'new-id','exposure=1.25')
    changed=catalog('c.lrcat',1.0,'new-id','exposure=0')
    assert edit_records(a)==edit_records(b)
    assert edit_records(a)!=edit_records(changed)


@pytest.mark.parametrize('allow_all', [False, True])
def test_export_without_selected_checkbox_requires_proven_all_public_catalog(tmp_path, allow_all):
    native=NativeCatalogs.__new__(NativeCatalogs)
    labels={3:'导出负片文件',4:'构建/包括智能预览',5:'包括可用的预览'}
    states={3:0,4:0,5:1}
    saved=[]
    native.menu=lambda *args:None
    native.dialog=lambda *args:99
    native.children=lambda window:[1,2,3,4,5]
    native.class_name=lambda window:'Edit' if window==1 else 'Button'
    native.text=lambda window:labels.get(window,'')
    native.user=SimpleNamespace(GetDlgCtrlID=lambda window:1148 if window==1 else 1 if window==2 else 100)
    native.send=lambda window,message,*args:states.get(window,0) if message==0xF0 else 1
    native.post=lambda *args:saved.append(args)
    if allow_all:
        native.export_selected(tmp_path/'output',allow_all=True)
        assert saved==[(2,0xF5)]
    else:
        with pytest.raises(RuntimeError,match='导出选项'):
            native.export_selected(tmp_path/'output')
        assert saved==[]
