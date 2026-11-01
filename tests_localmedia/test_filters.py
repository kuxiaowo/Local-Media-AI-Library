import os

from localmedia.api import filters
from localmedia.db import Database


def test_folder_filter_includes_descendants_not_prefix_siblings(tmp_path):
    db=Database(tmp_path)
    db.execute("INSERT INTO roots(id,path) VALUES ('r','root')")
    parent=os.path.join('root','100%_photos')
    paths=[parent,os.path.join(parent,'child'),parent+'other',os.path.join('root','100xxphotos','child')]
    for i,path in enumerate(paths):
        db.execute('INSERT INTO media(id,root_id,path,parent,name,kind,updated) VALUES (?,\'r\',?,?,?,\'image\',0)',(str(i),os.path.join(path,'p.jpg'),path,'p.jpg'))
    where,args=filters(parent=parent)
    assert {r['id'] for r in db.rows('SELECT id FROM media m WHERE '+where,args)}=={'0','1'}
