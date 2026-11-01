import hashlib
import os
import subprocess
import time

from fastapi.testclient import TestClient

from localmedia.api import MediaFileResponse, create_app
from localmedia.media import ffmpeg
from localmedia.playback import PlaybackCache


def test_hardware_playback_copy_and_cache_invalidation(tmp_path):
    source = tmp_path / 'source.mp4'
    subprocess.run([ffmpeg(), '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=320x180:rate=30', '-t', '1', '-c:v', 'libx264', str(source)], check=True)
    original = hashlib.sha256(source.read_bytes()).digest()
    cache = PlaybackCache(tmp_path / 'cache')
    try:
        target = cache.target(source)
        cache.request(source)
        deadline = time.monotonic() + 45
        while cache.request(source)['state'] in {'preparing', 'queued'} and time.monotonic() < deadline:
            time.sleep(.1)
        assert cache.request(source)['state'] == 'ready'
        print('Playback encoder:', cache.jobs[target.name]['encoder'])
        assert target.is_file()
        assert hashlib.sha256(source.read_bytes()).digest() == original
        assert cache.target(source) == target
        os.utime(source, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns + 1_000_000_000))
        assert cache.target(source) != target
    finally:
        cache.close()


def test_playback_and_original_support_byte_ranges(tmp_path):
    app = create_app(tmp_path / 'cache', background=False)
    db = app.state.db
    source = tmp_path / 'video.mp4'
    source.write_bytes(b'0123456789' * 100)
    db.execute("INSERT INTO roots(id,path) VALUES (?,?)", ('r', str(tmp_path)))
    db.execute("INSERT INTO media(id,root_id,path,parent,name,kind,availability,updated) VALUES (?,?,?,?,?,?,?,?)",
               ('v', 'r', str(source), str(tmp_path), source.name, 'video', 'online', time.time()))
    cache = PlaybackCache(db.directory)
    cache.target(source).write_bytes(b'abcdefghij' * 100)
    cache.close()
    with TestClient(app) as client:
        for endpoint, expected in [('original', b'2345'), ('playback', b'cdef')]:
            response = client.get(f'/api/media/v/{endpoint}', headers={'Range': 'bytes=2-5'})
            assert response.status_code == 206
            assert response.content == expected
            assert response.headers['content-range'] == 'bytes 2-5/1000'
        assert client.get('/api/media/v/playback', headers={'Range': 'bytes=2000-3000'}).status_code == 416
    assert MediaFileResponse.chunk_size == 1024 * 1024
