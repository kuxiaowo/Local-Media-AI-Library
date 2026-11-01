import json
import time

from fastapi.testclient import TestClient

from localmedia.api import create_app


def test_run_progress_keeps_queue_order_and_reports_video_phases(tmp_path):
    app = create_app(tmp_path / 'db', background=False)
    db = app.state.db
    with TestClient(app) as client:
        now = time.time()
        db.execute("INSERT INTO roots(id,path) VALUES ('root','root')")
        for ident, name, kind in [
            ('image-running', 'running.jpg', 'image'),
            ('video-running', 'running.mp4', 'video'),
            ('image-queued', 'queued.jpg', 'image'),
        ]:
            db.execute(
                "INSERT INTO media(id,root_id,path,parent,name,kind,updated) VALUES (?,?,?,?,?,?,?)",
                (ident, 'root', ident, 'root', name, kind, now),
            )
        db.execute("UPDATE media SET metadata=? WHERE id='video-running'", (json.dumps({'duration': 12}),))
        db.execute(
            "INSERT INTO runs(id,state,config,created) VALUES ('run','running','{}',?)",
            (now,),
        )
        config = json.dumps({'batch_size': 6, 'batch_overlap': 1, 'frame_interval': 5,
                             'video_frame_mode': 'quality'})
        checkpoint = json.dumps({
            'frames': [{'time': number} for number in range(13)],
            'scenes_done': True,
            'sample_next': 3,
            'batches': [{}, {}],
            'work_stage': 'ai',
        })
        tasks = [
            ('image-task', 'image-running', 'running', '{}', 4),
            ('video-task', 'video-running', 'running', checkpoint, 9),
            ('queued-task', 'image-queued', 'queued', '{}', 20),
        ]
        for ident, media_id, state, task_checkpoint, elapsed in tasks:
            db.execute(
                "INSERT INTO tasks(id,run_id,media_id,state,config,checkpoint,elapsed,updated) "
                "VALUES (?,'run',?,?,?,?,?,?)",
                (ident, media_id, state, config, task_checkpoint, elapsed, now - 1),
            )
        response = client.get('/api/runs/run/tasks')
        status = client.get('/api/runs')

    assert response.status_code == 200
    rows = response.json()
    assert [row['name'] for row in rows] == ['running.jpg', 'running.mp4', 'queued.jpg']
    elapsed = {row['name']: row['elapsed'] for row in rows}
    assert elapsed['running.jpg'] >= 4
    assert elapsed['running.mp4'] >= 9
    video = next(row for row in rows if row['kind'] == 'video')
    assert video['batch'] == {'current': 3, 'total': 3}
    assert video['stage'] == 'ai'
    assert video['extraction']['percent'] == 100
    assert rows[-1]['state'] == 'queued'
    assert status.json()['runs'][0]['phases'] == {'extracting': 0, 'ai': 1}
