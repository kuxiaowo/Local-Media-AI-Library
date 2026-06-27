from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.db_models import DirectoryRule, EmbeddingProfile, Job, MediaEmbedding, MediaFile
from app.workers import worker as worker_module
from app.workers.worker import WorkerManager


def test_worker_startup_recovers_embedding_pending_media_with_running_analysis_job(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    monkeypatch.setattr(worker_module, "SessionLocal", SessionLocal)

    with SessionLocal() as db:
        rule = DirectoryRule(
            path="F:/Videos",
            normalized_path="f:/videos",
            recursive=True,
            vision_model="vision-model",
            summary_model="summary-model",
            video_frame_strategy="hybrid",
            frame_interval_seconds=5,
            max_frames_per_video=12,
            video_frame_max_width=1280,
            video_batch_size=6,
            video_batch_overlap=1,
            analysis_detail="normal",
            enabled=True,
        )
        media = MediaFile(
            path="F:/Videos/input.mp4",
            normalized_path="f:/videos/input.mp4",
            root_path="f:/videos",
            parent_dir="f:/videos",
            media_type="video",
            status="embedding_pending",
            folder_rule=rule,
        )
        db.add_all([rule, media])
        db.flush()
        db.add(
            Job(
                job_type="analyze_video",
                status="running",
                target_id=media.id,
                target_path=media.path,
                payload={"stage": "final_summary"},
            )
        )
        db.commit()

    WorkerManager._recover_interrupted_completed_analysis_jobs()

    with SessionLocal() as db:
        jobs = list(db.scalars(select(Job).order_by(Job.created_at.asc())))

    assert [job.job_type for job in jobs] == ["analyze_video", "generate_embedding"]
    assert jobs[0].status == "completed"
    assert jobs[1].status == "queued"


def test_worker_startup_moves_legacy_done_media_without_embedding_back_to_pending(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    monkeypatch.setattr(worker_module, "SessionLocal", SessionLocal)

    with SessionLocal() as db:
        media = MediaFile(
            path="F:/Videos/input.mp4",
            normalized_path="f:/videos/input.mp4",
            root_path="f:/videos",
            parent_dir="f:/videos",
            media_type="video",
            status="done",
        )
        db.add(media)
        db.flush()
        db.add(
            Job(
                job_type="analyze_video",
                status="running",
                target_id=media.id,
                target_path=media.path,
                payload={"stage": "final_summary"},
            )
        )
        db.commit()

    WorkerManager._recover_interrupted_completed_analysis_jobs()

    with SessionLocal() as db:
        media = db.scalar(select(MediaFile))
        jobs = list(db.scalars(select(Job).order_by(Job.created_at.asc())))

    assert media is not None
    assert media.status == "embedding_pending"
    assert [job.job_type for job in jobs] == ["analyze_video", "generate_embedding"]
    assert jobs[0].status == "completed"
    assert jobs[1].status == "queued"


def test_worker_startup_moves_done_media_without_default_embedding_back_to_pending(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    monkeypatch.setattr(worker_module, "SessionLocal", SessionLocal)
    monkeypatch.setattr(
        worker_module,
        "get_settings",
        lambda: SimpleNamespace(default_embedding_model="current-embedding-model"),
    )

    with SessionLocal() as db:
        media = MediaFile(
            path="F:/Videos/input.mp4",
            normalized_path="f:/videos/input.mp4",
            root_path="f:/videos",
            parent_dir="f:/videos",
            media_type="video",
            status="done",
        )
        old_profile = EmbeddingProfile(model_name="old-embedding-model", dimension=2)
        db.add_all([media, old_profile])
        db.flush()
        db.add(
            MediaEmbedding(
                media_id=media.id,
                profile_id=old_profile.id,
                embedding=[0.1, 0.2],
                embedded_text="old searchable text",
            )
        )
        db.commit()

    WorkerManager._recover_done_media_missing_default_embedding()

    with SessionLocal() as db:
        media = db.scalar(select(MediaFile))
        jobs = list(db.scalars(select(Job)))

    assert media is not None
    assert media.status == "embedding_pending"
    assert len(jobs) == 1
    assert jobs[0].job_type == "generate_embedding"
    assert jobs[0].status == "queued"


def test_worker_startup_supersedes_orphan_running_job_when_media_has_default_embedding(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    monkeypatch.setattr(worker_module, "SessionLocal", SessionLocal)
    monkeypatch.setattr(
        worker_module,
        "get_settings",
        lambda: SimpleNamespace(default_embedding_model="current-embedding-model"),
    )

    with SessionLocal() as db:
        media = MediaFile(
            path="F:/Videos/input.mp4",
            normalized_path="f:/videos/input.mp4",
            root_path="f:/videos",
            parent_dir="f:/videos",
            media_type="video",
            status="analyzing",
        )
        profile = EmbeddingProfile(model_name="current-embedding-model", dimension=2)
        db.add_all([media, profile])
        db.flush()
        db.add_all(
            [
                MediaEmbedding(
                    media_id=media.id,
                    profile_id=profile.id,
                    embedding=[0.1, 0.2],
                    embedded_text="searchable text",
                ),
                Job(
                    job_type="analyze_video",
                    status="running",
                    target_id=media.id,
                    target_path=media.path,
                    progress_current=25,
                    progress_total=25,
                    payload={"stage": "final_summary", "resume_segments": True},
                ),
            ]
        )
        db.commit()

    WorkerManager._recover_interrupted_active_media_jobs()

    with SessionLocal() as db:
        media = db.scalar(select(MediaFile))
        job = db.scalar(select(Job))

    assert media is not None
    assert media.status == "done"
    assert media.error_message is None
    assert job is not None
    assert job.status == "superseded"
    assert job.finished_at is not None


def test_worker_startup_fails_orphan_running_job_when_media_has_no_default_embedding(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    monkeypatch.setattr(worker_module, "SessionLocal", SessionLocal)
    monkeypatch.setattr(
        worker_module,
        "get_settings",
        lambda: SimpleNamespace(default_embedding_model="current-embedding-model"),
    )

    with SessionLocal() as db:
        media = MediaFile(
            path="F:/Videos/input.mp4",
            normalized_path="f:/videos/input.mp4",
            root_path="f:/videos",
            parent_dir="f:/videos",
            media_type="video",
            status="analyzing",
        )
        db.add(media)
        db.flush()
        db.add(
            Job(
                job_type="analyze_video",
                status="running",
                target_id=media.id,
                target_path=media.path,
                progress_current=17,
                progress_total=34,
                payload={"stage": "analyze_segments", "resume_segments": True},
            )
        )
        db.commit()

    WorkerManager._recover_interrupted_active_media_jobs()

    with SessionLocal() as db:
        media = db.scalar(select(MediaFile))
        job = db.scalar(select(Job))

    assert media is not None
    assert media.status == "failed"
    assert media.error_message == "Worker was interrupted while analyze_video was running"
    assert job is not None
    assert job.status == "failed"
    assert job.error_message == "Worker was interrupted while analyze_video was running"


def test_worker_startup_recovers_failed_media_with_running_job(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    monkeypatch.setattr(worker_module, "SessionLocal", SessionLocal)

    with SessionLocal() as db:
        media = MediaFile(
            path="F:/Videos/input.mp4",
            normalized_path="f:/videos/input.mp4",
            root_path="f:/videos",
            parent_dir="f:/videos",
            media_type="video",
            status="failed",
            error_message="All connection attempts failed",
        )
        db.add(media)
        db.flush()
        db.add(
            Job(
                job_type="analyze_video",
                status="running",
                target_id=media.id,
                target_path=media.path,
                payload={"stage": "analyze_segments"},
            )
        )
        db.commit()

    WorkerManager._recover_interrupted_failed_media_jobs()

    with SessionLocal() as db:
        job = db.scalar(select(Job))

    assert job is not None
    assert job.status == "failed"
    assert job.error_message == "All connection attempts failed"


def test_extract_metadata_job_with_run_ai_false_does_not_queue_analysis(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)

    def fake_extract_image_metadata(db, media):
        media.status = "metadata_done"
        media.error_message = None
        db.add(media)
        return media

    monkeypatch.setattr(worker_module, "extract_image_metadata", fake_extract_image_metadata)

    with SessionLocal() as db:
        rule = DirectoryRule(
            path="F:/Photos",
            normalized_path="f:/photos",
            recursive=True,
            vision_model="vision-model",
            summary_model="summary-model",
            video_frame_strategy="hybrid",
            frame_interval_seconds=5,
            max_frames_per_video=12,
            video_frame_max_width=1280,
            video_batch_size=6,
            video_batch_overlap=1,
            analysis_detail="normal",
            enabled=True,
        )
        media = MediaFile(
            path="F:/Photos/input.jpg",
            normalized_path="f:/photos/input.jpg",
            root_path="f:/photos",
            parent_dir="f:/photos",
            media_type="image",
            status="pending",
            folder_rule=rule,
        )
        db.add_all([rule, media])
        db.flush()
        job = Job(
            job_type="extract_metadata",
            status="running",
            target_id=media.id,
            target_path=media.path,
            payload={"run_ai": False},
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        WorkerManager(pools=[])._execute_job(db, job)
        db.refresh(media)
        jobs = list(db.scalars(select(Job).order_by(Job.created_at.asc())).all())

    assert media.status == "metadata_done"
    assert [job.job_type for job in jobs] == ["extract_metadata"]
