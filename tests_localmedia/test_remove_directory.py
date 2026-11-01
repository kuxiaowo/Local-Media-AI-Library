import os
import time
from PIL import Image
from fastapi.testclient import TestClient
from localmedia.api import create_app
from localmedia.scanner import Scanner


def test_remove_nested_directory_delays_cleanup_and_excludes_rescan(tmp_path):
    parent=tmp_path/'photos'
    child=parent/'trip'
    child.mkdir(parents=True)
    image=child/'a.jpg'
    Image.new('RGB',(8,8)).save(image)
    os.utime(image,(time.time()-10,time.time()-10))
    app=create_app(tmp_path/'cache',background=False)
    db=app.state.db
    scanner=Scanner(db)
    with TestClient(app) as client:
        client.post('/api/roots',json={'path':str(parent)})
        scanner.scan()
        row=db.one('SELECT * FROM media')
        cache=db.directory/'previews'
        cache.mkdir()
        preview=cache/(row['id']+'-sample.jpg')
        preview.write_bytes(b'preview')
        result=client.post('/api/directories/remove',json={'path':str(child)})
        assert result.status_code==200
        assert result.json()['removed']==1
        assert image.exists() and preview.exists()
        scanner.scan()
        assert not db.rows('SELECT * FROM media')
        scanner.cleanup_previews()
        assert preview.exists()
        assert db.one('SELECT due FROM preview_cleanup')['due']>time.time()+1790
        db.execute('UPDATE preview_cleanup SET due=0')
        Scanner(db).cleanup_previews()
        assert not preview.exists() and image.exists()
        assert client.post('/api/roots',json={'path':str(child)}).status_code==200
        scanner.scan()
        assert len(db.rows('SELECT * FROM media'))==1
