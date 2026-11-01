import asyncio
import json
import os
import time

from fastapi.testclient import TestClient

from localmedia.api import create_app
from localmedia.worker_service import WorkerClient


def test_analysis_worker_is_separate_and_accepts_cancellation(tmp_path):
    app = create_app(tmp_path / 'library', separate_worker=True)
    with TestClient(app) as client:
        process = app.state.worker_process
        assert process.pid != os.getpid()
        assert process.is_alive()
        assert client.get('/api/health').status_code == 200
        assert client.patch('/api/settings', json={'max_cpu_cores': 1}).status_code == 200
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            row = app.state.db.one("SELECT value FROM worker_status WHERE key='cpu'")
            if row and json.loads(row['value']).get('requested') == 1:
                break
            time.sleep(.1)
        cpu_status = json.loads(row['value'])
        assert cpu_status['applied'] == 1
        assert cpu_status['processes'] >= 1
        asyncio.run(WorkerClient(app.state.db).cancel(run_id='not-a-run'))
        process.terminate()
        process.join(5)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            replacement = app.state.worker_process
            if replacement.pid != process.pid and replacement.is_alive():
                break
            time.sleep(.2)
        assert replacement.pid != process.pid
        assert replacement.is_alive()
        assert client.get('/api/health').status_code == 200
        asyncio.run(WorkerClient(app.state.db).cancel(run_id='not-a-run'))
