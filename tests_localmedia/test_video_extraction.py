import asyncio
import json
import shutil
import subprocess

import pytest
from PIL import Image

from localmedia.db import Database
from localmedia.frame_cache import complete as frame_cache_complete
from localmedia.media import file_hash, make_preview, video_decode_options, video_decoder_candidates
from localmedia.tasks import Tasks
from localmedia.worker import Worker


@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='FFmpeg not installed')
def test_video_preview_uses_tenth_second(tmp_path):
    path = tmp_path / 'changing.mp4'
    subprocess.run([
        'ffmpeg', '-v', 'error',
        '-f', 'lavfi', '-i', 'color=c=black:s=64x64:r=2:d=10',
        '-f', 'lavfi', '-i', 'color=c=red:s=64x64:r=2:d=2',
        '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path),
    ], check=True)
    preview = tmp_path / 'preview.jpg'
    make_preview(path, preview, 'video')
    with Image.open(preview) as image:
        red, green, blue = image.convert('RGB').getpixel((image.width // 2, image.height // 2))
    assert red > 180 and green < 80 and blue < 80


@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='FFmpeg not installed')
@pytest.mark.parametrize('duration,expected_samples,expected_segments',[(12,3,1),(72,15,2)])
def test_no_scene_change_video_extracts_and_resumes(tmp_path,duration,expected_samples,expected_segments):
    path=tmp_path/'video.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=160x120:r=10','-t',str(duration),'-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True)
    db=Database(tmp_path/'cache')
    meta=make_preview(path,tmp_path/'preview.jpg','video')
    db.execute("INSERT INTO roots(id,path) VALUES ('r','root')")
    db.execute("INSERT INTO media(id,root_id,path,parent,name,kind,hash,metadata,updated) VALUES ('m','r',?,'root','video.mp4','video',?,?,0)", (str(path),file_hash(path),json.dumps(meta)))
    tasks=Tasks(db);tasks.create(['m']);task=tasks.claim();worker=Worker(db,tasks)
    cp={}
    frames=asyncio.run(worker.frames(task,cp,db.one('SELECT * FROM media'),db.settings()))
    assert len(frames)>=1 and cp['sample_next']==expected_samples and cp['scenes_done']
    assert cp['scene_segment']==expected_segments
    stored = db.rows('SELECT kind,time,path FROM video_frames WHERE media_id=? ORDER BY time,id', ('m',))
    assert len(stored) == len(frames)
    assert all(frame['kind'] in {'interval', 'scene'} for frame in stored)
    elapsed=db.one('SELECT elapsed FROM tasks')['elapsed']
    again=asyncio.run(worker.frames(task,cp,db.one('SELECT * FROM media'),db.settings()))
    assert again==frames
    assert db.one('SELECT elapsed FROM tasks')['elapsed']==elapsed


def test_scene_sampling_rate_is_configurable(tmp_path):
    db = Database(tmp_path / 'cache')
    assert db.settings()['scene_fps'] == 3
    assert db.settings()['video_frame_mode'] == 'quality'
    db.set_setting('scene_fps', 2)
    assert db.settings()['scene_fps'] == 2


@pytest.mark.parametrize('mode', ['fast', 'quality'])
def test_complete_frame_cache_is_skipped_but_missing_files_continue(tmp_path, mode):
    db = Database(tmp_path / 'cache')
    source = tmp_path / 'movie.mp4'
    source.write_bytes(b'video')
    digest = file_hash(source)
    db.execute("INSERT INTO roots(id,path) VALUES ('r','root')")
    db.execute(
        "INSERT INTO media(id,root_id,path,parent,name,kind,hash,metadata,analysis_state,updated) "
        "VALUES ('m','r',?,'root','movie.mp4','video',?,?,'done',0)",
        (str(source), digest, json.dumps({'duration': 12})),
    )
    db.set_setting('video_frame_mode', mode)
    config = db.settings()
    cached_paths = []
    for index, stamp in enumerate((0, 5, 10)):
        path = tmp_path / f'interval-{index}.jpg'
        Image.new('RGB', (8, 8), 'blue').save(path)
        cached_paths.append(path)
        db.execute(
            "INSERT INTO video_frames(media_id,kind,time,path,created,source_hash,extraction_profile) "
            "VALUES ('m','interval',?,?,0,?,?)",
            (stamp, str(path), digest, Worker.frame_profile(config)),
        )
    if mode == 'quality':
        scene = tmp_path / 'scene.jpg'
        Image.new('RGB', (8, 8), 'red').save(scene)
        profile = Worker.frame_profile(config)
        db.execute(
            "INSERT INTO video_frames(media_id,kind,time,path,created,source_hash,extraction_profile) "
            "VALUES ('m','scene',0,?,0,?,?)",
            (str(scene), digest, profile),
        )
        db.execute(
            "INSERT INTO video_frame_segments(media_id,source_hash,extraction_profile,segment,completed) "
            "VALUES ('m',?,?,0,0)",
            (digest, profile),
        )

    media = db.one("SELECT * FROM media WHERE id='m'")
    assert frame_cache_complete(db, media, config)
    completed_run = Tasks(db).create(['m'], run_kind='frames')
    assert db.one('SELECT COUNT(*) AS count FROM tasks WHERE run_id=?', (completed_run,))['count'] == 0
    assert db.one('SELECT state FROM runs WHERE id=?', (completed_run,))['state'] == 'completed'

    cached_paths[-1].unlink()
    assert not frame_cache_complete(db, media, config)
    incomplete_run = Tasks(db).create(['m'], run_kind='frames')
    assert db.one('SELECT COUNT(*) AS count FROM tasks WHERE run_id=?', (incomplete_run,))['count'] == 1


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg not installed')
def test_fast_mode_extracts_only_interval_frames(tmp_path):
    path = tmp_path / 'changing.mp4'
    subprocess.run([
        'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'testsrc2=s=160x120:r=10:d=12', '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p', str(path),
    ], check=True)
    db = Database(tmp_path / 'cache')
    meta = make_preview(path, tmp_path / 'preview.jpg', 'video')
    db.execute("INSERT INTO roots(id,path) VALUES ('r','root')")
    db.execute("INSERT INTO media(id,root_id,path,parent,name,kind,hash,metadata,updated) VALUES ('m','r',?,'root','changing.mp4','video',?,?,0)",
               (str(path), file_hash(path), json.dumps(meta)))
    db.set_setting('video_frame_mode', 'fast')
    tasks = Tasks(db)
    tasks.create(['m'])
    task = tasks.claim()
    config = json.loads(task['config'])
    stale_scene = tmp_path / 'old-scene.jpg'
    Image.new('RGB', (32, 32), 'red').save(stale_scene)
    db.execute(
        'INSERT INTO video_frames(media_id,task_id,kind,time,path,created,source_hash,extraction_profile) '
        'VALUES (?,?,?,?,?,?,?,?)',
        ('m', None, 'scene', 2.5, str(stale_scene), 0, task['source_hash'], Worker.frame_profile(config)),
    )
    cp = {}

    frames = asyncio.run(Worker(db, tasks).frames(
        task, cp, db.one('SELECT * FROM media'), config))

    assert config['video_frame_mode'] == 'fast'
    assert [frame['time'] for frame in frames] == [0, 5, 10]
    assert {frame['kind'] for frame in frames} == {'interval'}
    assert cp['scene_detection_skipped'] is True
    assert cp['scenes_done'] is True
    assert not db.rows('SELECT * FROM video_frame_segments')


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg not installed')
def test_declared_duration_beyond_decodable_tail_skips_only_last_sample(tmp_path):
    path = tmp_path / 'short.mp4'
    subprocess.run([
        'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'color=c=blue:s=160x120:r=10:d=12', '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p', str(path),
    ], check=True)
    db = Database(tmp_path / 'cache')
    meta = make_preview(path, tmp_path / 'preview.jpg', 'video')
    meta['duration'] = 40
    db.execute("INSERT INTO roots(id,path) VALUES ('r','root')")
    db.execute("INSERT INTO media(id,root_id,path,parent,name,kind,hash,metadata,updated) VALUES ('m','r',?,'root','short.mp4','video',?,?,0)",
               (str(path), file_hash(path), json.dumps(meta)))
    db.set_setting('frame_interval', 30)
    tasks = Tasks(db)
    tasks.create(['m'])
    task = tasks.claim()
    worker = Worker(db, tasks)
    cp = {}

    frames = asyncio.run(worker.frames(task, cp, db.one('SELECT * FROM media'), db.settings()))

    assert cp['sample_next'] == 2
    assert cp['decode_duration'] == 30
    assert cp['scenes_done']
    assert any(frame['kind'] == 'interval' and frame['time'] == 0 for frame in frames)
    assert not any(frame['kind'] == 'interval' and frame['time'] == 30 for frame in frames)
    assert not cp.get('video_decoder_fallback')


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg not installed')
def test_rebuilt_task_reuses_frames_and_completed_scene_segments(tmp_path):
    path = tmp_path / 'cached.mp4'
    subprocess.run([
        'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'color=c=green:s=160x120:r=10:d=12', '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p', str(path),
    ], check=True)
    db = Database(tmp_path / 'cache')
    meta = make_preview(path, tmp_path / 'preview.jpg', 'video')
    db.execute("INSERT INTO roots(id,path) VALUES ('r','root')")
    db.execute("INSERT INTO media(id,root_id,path,parent,name,kind,hash,metadata,updated) VALUES ('m','r',?,'root','cached.mp4','video',?,?,0)",
               (str(path), file_hash(path), json.dumps(meta)))
    tasks = Tasks(db)
    first_run = tasks.create(['m'])
    first_task = tasks.claim()
    first_cp = {}
    first_frames = asyncio.run(Worker(db, tasks).frames(
        first_task, first_cp, db.one('SELECT * FROM media'), db.settings()))
    assert first_cp['scenes_done']
    assert db.one('SELECT COUNT(*) AS count FROM video_frame_segments')['count'] == 1

    db.execute('DELETE FROM tasks WHERE run_id=?', (first_run,))
    db.execute('DELETE FROM runs WHERE id=?', (first_run,))
    second_run = tasks.create(['m'])
    second_task = tasks.claim()
    second_cp = {}
    worker = Worker(db, tasks)

    async def forbidden_decode(*args, **kwargs):
        raise AssertionError('cached frames must not invoke FFmpeg decoding')

    worker.video_process = forbidden_decode
    second_frames = asyncio.run(worker.frames(
        second_task, second_cp, db.one('SELECT * FROM media'), db.settings()))

    assert second_run != first_run
    assert second_cp['reused_frames'] == len(first_frames)
    assert second_cp['sample_next'] == 3
    assert second_cp['scene_segment'] == 1
    assert second_cp['scenes_done']
    assert second_frames == first_frames


def test_video_decoder_modes_and_defaults(tmp_path):
    db = Database(tmp_path / 'cache')
    assert db.settings()['video_decoder'] == 'cpu'
    assert video_decode_options('cpu', 'av1') == []
    assert video_decode_options('intel', 'av1') == ['-c:v', 'av1_qsv']
    assert video_decode_options('nvidia', 'av1') == ['-hwaccel', 'cuda']
    assert video_decoder_candidates('intel_nvidia') == ['nvidia', 'intel']
    assert video_decoder_candidates('cpu_intel_nvidia') == ['nvidia', 'intel', 'cpu']
    with pytest.raises(ValueError):
        video_decode_options('unknown', 'av1')


def test_combined_decoder_round_robin(tmp_path):
    db = Database(tmp_path / 'cache')
    worker = Worker(db, Tasks(db))
    commands = []

    async def successful_process(command):
        commands.append(command)
        return b'', b''

    worker.process = successful_process
    async def immediate_stage(task, checkpoint, operation, **kwargs):
        return await operation()
    worker.stage = immediate_stage
    config = {'video_decoder': 'intel_nvidia'}
    checkpoint = {}
    task = {'id': 'task'}
    asyncio.run(worker.video_process(task, [], 'sample.mp4', ['-f', 'null', 'NUL'], config, checkpoint, 'av1'))
    asyncio.run(worker.video_process(task, [], 'sample.mp4', ['-f', 'null', 'NUL'], config, checkpoint, 'av1'))

    assert ['-hwaccel', 'cuda'] == commands[0][1:3]
    assert ['-c:v', 'av1_qsv'] == commands[1][1:3]
    assert checkpoint['video_decoder_usage'] == {'nvidia': 1, 'intel': 1}
