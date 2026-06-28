from __future__ import annotations

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.api.routes_jobs import clear_jobs, retry_job
from app.database import Base
from app.models.db_models import Job, MediaFile


def test_clear_jobs_removes_all_jobs() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        db.add_all(
            [
                Job(job_type="scan_directory", status="queued", payload={}),
                Job(job_type="extract_metadata", status="running", payload={}),
                Job(job_type="generate_embedding", status="failed", payload={}),
                Job(job_type="cleanup_stale_media", status="completed", payload={}),
            ]
        )
        db.commit()

        response = clear_jobs(db=db)
        remaining = db.scalar(select(func.count(Job.id))) or 0

    assert response.deleted == 4
    assert remaining == 0


def test_retry_video_analysis_marks_job_for_segment_resume() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        failed_job = Job(
            job_type="analyze_video",
            status="failed",
            target_path="F:/Videos/input.mp4",
            payload={"stage": "analyze_segments"},
        )
        db.add(failed_job)
        db.commit()
        db.refresh(failed_job)

        retry = retry_job(failed_job.id, db=db)

    assert retry.job_type == "analyze_video"
    assert retry.status == "queued"
    assert retry.payload == {"stage": "analyze_segments", "resume_segments": True}


def test_retry_media_job_clears_failed_media_state() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        media = MediaFile(
            path="F:/Videos/input.mp4",
            normalized_path="f:/videos/input.mp4",
            media_type="video",
            status="failed",
            error_message="old connection error",
        )
        db.add(media)
        db.flush()
        failed_job = Job(
            job_type="analyze_video",
            status="failed",
            target_id=media.id,
            target_path=media.path,
            payload={"stage": "analyze_segments"},
        )
        db.add(failed_job)
        db.commit()
        db.refresh(failed_job)

        retry_job(failed_job.id, db=db)
        db.refresh(media)

    assert media.status == "metadata_done"
    assert media.error_message is None


def test_retry_legacy_embedding_job_queues_reanalysis() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        media = MediaFile(
            path="F:/Videos/input.mp4",
            normalized_path="f:/videos/input.mp4",
            media_type="video",
            status="failed",
            error_message="embedding failed",
        )
        db.add(media)
        db.flush()
        failed_job = Job(
            job_type="generate_embedding",
            status="failed",
            target_id=media.id,
            target_path=media.path,
            payload={},
        )
        db.add(failed_job)
        db.commit()
        db.refresh(failed_job)

        retry = retry_job(failed_job.id, db=db)
        db.refresh(media)

    assert retry.job_type == "reanalyze_media"
    assert media.status == "needs_reanalysis"
    assert media.error_message is None
