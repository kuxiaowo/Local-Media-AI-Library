import asyncio
import io
import uuid
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from localmedia.api import create_app
from localmedia.lightroom_bridge import catalog_id
from localmedia.tasks import Tasks


def jpeg(color='red'):
    buffer = io.BytesIO()
    Image.new('RGB', (120, 80), color).save(buffer, format='JPEG')
    return buffer.getvalue()


def config(tmp_path, name='A'):
    return dict(catalog_id=catalog_id(tmp_path / (name + '.lrcat')),
                token='test-token-' + '0' * 32, inbox=str(tmp_path / name / 'renders'))


@pytest.fixture
def bridge(tmp_path):
    settings = config(tmp_path)
    app = create_app(tmp_path / 'A' / 'db', background=False, lightroom=settings)
    headers = {'X-LocalMedia-Token': settings['token'], 'X-LocalMedia-Catalog': settings['catalog_id']}
    with TestClient(app, headers=headers) as client:
        yield app, client


def ingest(client, ident=None, data=None):
    ident = ident or str(uuid.uuid4())
    response = client.post('/api/lightroom/ingest', params={'photo': ident}, content=data or jpeg())
    assert response.status_code == 200, response.text
    return ident, response.text.splitlines()[1]


def test_bridge_is_opt_in(tmp_path):
    with TestClient(create_app(tmp_path, background=False)) as client:
        assert client.get('/api/lightroom/health').status_code == 404


def test_authorization_and_catalog_must_match(bridge):
    _, client = bridge
    assert client.get('/api/lightroom/health').status_code == 200
    assert client.get('/api/lightroom/health', headers={'X-LocalMedia-Token': ''}).status_code == 403
    assert client.get('/api/lightroom/health', headers={'X-LocalMedia-Catalog': 'other'}).status_code == 409
    assert client.post('/api/lightroom/ingest', params={'photo': '../outside'}, content=jpeg()).status_code == 400


def test_reimport_is_idempotent_and_virtual_copies_have_independent_ids(bridge):
    app, client = bridge
    ident, media_id = ingest(client)
    assert ingest(client, ident)[1] == media_id
    assert app.state.db.one('SELECT revision FROM media')['revision'] == 0
    _, copy_id = ingest(client)
    assert copy_id != media_id
    ingest(client, ident, jpeg('blue'))
    assert app.state.db.one('SELECT revision FROM media WHERE id=?', (media_id,))['revision'] == 1


def test_invalid_image_never_creates_media(bridge):
    app, client = bridge
    result = client.post('/api/lightroom/ingest', params={'photo': str(uuid.uuid4())}, content=b'not jpeg')
    assert result.status_code == 400
    assert app.state.db.rows('SELECT * FROM media') == []


def test_original_filename_is_preserved_without_using_it_as_storage_path(bridge):
    app, client = bridge
    ident = str(uuid.uuid4())
    response = client.post('/api/lightroom/ingest', params={'photo':ident, 'filename':'合成图形.jpg'}, content=jpeg())
    assert response.status_code == 200
    row = app.state.db.one('SELECT * FROM media')
    assert row['name'] == '合成图形.jpg' and row['path'].endswith(ident + '.jpg')
    assert client.post('/api/lightroom/ingest', params={'photo':ident, 'filename':'../outside.jpg'}, content=jpeg()).status_code == 400


def test_inflight_analysis_prevents_replacing_render(bridge):
    app, client = bridge
    ident, _ = ingest(client)
    assert client.post('/api/lightroom/analyze', params={'photo': ident}).status_code == 200
    result = client.post('/api/lightroom/ingest', params={'photo': ident}, content=jpeg('blue'))
    assert result.status_code == 409
    assert app.state.db.one('SELECT revision FROM media')['revision'] == 0


def test_cloud_analysis_requires_existing_explicit_ack(bridge):
    app, client = bridge
    ident, _ = ingest(client)
    app.state.db.set_setting('provider', 'openai')
    assert client.get('/api/lightroom/policy').text == 'ok\ncloud'
    assert client.post('/api/lightroom/analyze', params={'photo': ident}).status_code == 400
    assert app.state.db.rows('SELECT * FROM tasks') == []
    assert client.post('/api/lightroom/analyze', params={'photo': ident, 'cloud_ack': True}).status_code == 200


def test_failed_analysis_is_reported_instead_of_waiting_forever(bridge):
    app, client = bridge
    ident, _ = ingest(client)
    client.post('/api/lightroom/analyze', params={'photo': ident})
    app.state.db.execute("UPDATE tasks SET state='failed',error='模型输出不完整'")
    result = client.get('/api/lightroom/description', params={'photo': ident}).text.split('\n')
    assert result[:2] == ['ok', 'failed']
    assert unquote(result[2]) == '模型输出不完整'


def test_render_analysis_description_and_search_use_existing_pipeline(bridge):
    app, client = bridge
    ident, _ = ingest(client)
    scanner, db, worker = app.state.scanner, app.state.db, app.state.worker
    scanner.preview(db.one('SELECT * FROM media'))
    scanner.hash_one(db.one('SELECT * FROM media'))
    seen = []

    async def describe(config, prompt, images=(), summary=False):
        seen.extend(images)
        return '红色矩形\n合成图片，包含百分号 % 和制表符\t。'

    async def embed(config, text):
        return [1, 0, 0]

    worker.providers.describe = describe
    worker.providers.embed = embed
    assert client.post('/api/lightroom/analyze', params={'photo': ident}).status_code == 200
    asyncio.run(worker.analyze(Tasks(db).claim()))
    assert seen and all(str(db.directory) in str(path) for path in seen)
    result = client.get('/api/lightroom/description', params={'photo': ident}).text.split('\n')
    assert result[:2] == ['ok', 'done']
    assert unquote(result[2]) == '红色矩形\n合成图片，包含百分号 % 和制表符\t。'
    found = client.get('/api/lightroom/search', params={'q': '红色'} )
    assert found.status_code == 200, found.text
    assert found.text.splitlines()[1].split('\t')[0] == ident


def test_databases_cannot_be_rebound_to_another_catalog(tmp_path):
    create_app(tmp_path / 'db', background=False, lightroom=config(tmp_path, 'A'))
    with pytest.raises(ValueError, match='another Lightroom catalog'):
        create_app(tmp_path / 'db', background=False, lightroom=config(tmp_path, 'B'))


def test_same_photo_uuid_is_isolated_between_catalogs(tmp_path):
    ident = str(uuid.uuid4())
    for name, expected in [('A', 'first catalog'), ('B', 'second catalog')]:
        settings = config(tmp_path, name)
        app = create_app(tmp_path / name / 'db', background=False, lightroom=settings)
        with TestClient(app, headers={'X-LocalMedia-Token':settings['token'], 'X-LocalMedia-Catalog':settings['catalog_id']}) as client:
            _, media_id = ingest(client, ident)
            app.state.db.execute("UPDATE media SET description=?,analysis_state='done' WHERE id=?", (expected, media_id))
            assert unquote(client.get('/api/lightroom/description', params={'photo':ident}).text.splitlines()[2]) == expected
