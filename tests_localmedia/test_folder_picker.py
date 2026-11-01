from fastapi.testclient import TestClient
from localmedia.api import create_app


def test_folder_selection_cancel_and_origin(tmp_path, monkeypatch):
    calls=[]
    async def choose(initial):
        calls.append(initial)
        return 'D:/照片' if initial else None
    monkeypatch.setattr('localmedia.api.choose_folder',choose)
    app=create_app(tmp_path,background=False)
    with TestClient(app) as client:
        assert client.post('/api/directories/pick',json={'text':'D:/'}).json()=={'path':'D:/照片'}
        assert client.post('/api/directories/pick',json={'text':''}).json()=={'path':None}
        assert client.post('/api/directories/pick',json={'text':''},headers={'origin':'https://outside.example'}).status_code==403
    assert calls==['D:/','']
    assert app.state.db.rows('SELECT * FROM roots')==[]
