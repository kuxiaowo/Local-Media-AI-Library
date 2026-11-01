from __future__ import annotations

import threading

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.db_models import Job
from app.workers import worker as worker_module
from app.workers.worker import WorkerManager, WorkerPoolConfig


def test_pause_prevents_dispatching_queued_jobs(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    monkeypatch.setattr(worker_module, "SessionLocal", SessionLocal)

    manager = WorkerManager(pools=[WorkerPoolConfig(("scan_directory",), 1, "scan")])
    manager.pause()

    with SessionLocal() as db:
        db.add(Job(job_type="scan_directory", status="queued", payload={}))
        db.commit()

    manager._dispatch_once()

    assert manager._queues["scan"].qsize() == 0
    assert manager._queued_job_ids == set()


def test_worker_does_not_start_prefetched_job_while_paused(monkeypatch) -> None:
    manager = WorkerManager(poll_seconds=0.01)
    queue = manager._queues["scan"]
    started_jobs: list[str] = []

    def fake_run_job(job_id: str) -> None:
        started_jobs.append(job_id)

    monkeypatch.setattr(manager, "_run_job", fake_run_job)
    manager.pause()
    queue.put("job-1")
    manager._queued_job_ids.add("job-1")

    stop_event = threading.Event()
    thread = threading.Thread(target=manager._worker_loop, args=(queue, stop_event))
    thread.start()
    stop_event.wait(0.05)
    stop_event.set()
    thread.join(timeout=1)

    assert started_jobs == []
    assert queue.qsize() == 1
    assert manager._queued_job_ids == {"job-1"}


def test_run_job_leaves_job_queued_when_pause_wins_race(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    monkeypatch.setattr(worker_module, "SessionLocal", SessionLocal)

    manager = WorkerManager(pools=[])

    with SessionLocal() as db:
        job = Job(job_type="scan_directory", status="queued", payload={})
        db.add(job)
        db.commit()
        job_id = str(job.id)

    manager.pause()
    manager._run_job(job_id)

    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.id == job_id))

    assert job is not None
    assert job.status == "queued"
