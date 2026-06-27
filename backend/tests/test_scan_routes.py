from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes_scan import generate_ai_records, get_media_queue
from app.database import Base
from app.models.db_models import DirectoryRule, Job, MediaFile
from app.models.schemas import GenerateAiRecordsRequest


def test_media_queue_only_lists_active_or_failed_jobs() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

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
        queued_media = MediaFile(
            path="F:/Photos/queued.jpg",
            normalized_path="f:/photos/queued.jpg",
            root_path="f:/photos",
            parent_dir="f:/photos",
            media_type="image",
            status="pending",
            folder_rule=rule,
        )
        stale_media = MediaFile(
            path="F:/Photos/stale.jpg",
            normalized_path="f:/photos/stale.jpg",
            root_path="f:/photos",
            parent_dir="f:/photos",
            media_type="image",
            status="needs_reanalysis",
            folder_rule=rule,
        )
        completed_media = MediaFile(
            path="F:/Photos/completed.jpg",
            normalized_path="f:/photos/completed.jpg",
            root_path="f:/photos",
            parent_dir="f:/photos",
            media_type="image",
            status="done",
            folder_rule=rule,
        )
        db.add_all([rule, queued_media, stale_media, completed_media])
        db.flush()
        db.add_all(
            [
                Job(
                    job_type="extract_metadata",
                    status="queued",
                    target_id=queued_media.id,
                    target_path=queued_media.path,
                    payload={},
                ),
                Job(
                    job_type="generate_embedding",
                    status="completed",
                    target_id=completed_media.id,
                    target_path=completed_media.path,
                    payload={},
                ),
            ]
        )
        db.commit()

        response = get_media_queue(db=db)

    assert response.total == 1
    assert len(response.items) == 1
    assert response.items[0].path == "F:/Photos/queued.jpg"
    assert response.items[0].job_status == "queued"


def test_media_queue_prefers_embedding_job_after_analysis_done() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

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
        db.add_all(
            [
                Job(
                    job_type="analyze_video",
                    status="running",
                    target_id=media.id,
                    target_path=media.path,
                    progress_current=3,
                    progress_total=3,
                    payload={"stage": "queue_embedding"},
                ),
                Job(
                    job_type="generate_embedding",
                    status="queued",
                    target_id=media.id,
                    target_path=media.path,
                    payload={},
                ),
            ]
        )
        db.commit()

        response = get_media_queue(db=db)

    assert response.total == 1
    assert response.items[0].media_status == "embedding_pending"
    assert response.items[0].job_type == "generate_embedding"
    assert response.items[0].job_status == "queued"


def test_media_queue_hides_failed_job_superseded_by_done_media() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

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
            path="F:/Videos/done.mp4",
            normalized_path="f:/videos/done.mp4",
            root_path="f:/videos",
            parent_dir="f:/videos",
            media_type="video",
            status="done",
            folder_rule=rule,
        )
        db.add_all([rule, media])
        db.flush()
        db.add(
            Job(
                job_type="analyze_video",
                status="failed",
                target_id=media.id,
                target_path=media.path,
                error_message="old connection error",
                payload={"stage": "analyze_segments"},
            )
        )
        db.commit()

        response = get_media_queue(db=db)

    assert response.total == 0
    assert response.items == []


def test_media_queue_keeps_failed_embedding_job_for_embedding_pending_media() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

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
            path="F:/Videos/embedding.mp4",
            normalized_path="f:/videos/embedding.mp4",
            root_path="f:/videos",
            parent_dir="f:/videos",
            media_type="video",
            status="embedding_pending",
            folder_rule=rule,
        )
        db.add_all([rule, media])
        db.flush()
        db.add_all(
            [
                Job(
                    job_type="analyze_video",
                    status="failed",
                    target_id=media.id,
                    target_path=media.path,
                    error_message="old analysis error",
                    payload={"stage": "analyze_segments"},
                ),
                Job(
                    job_type="generate_embedding",
                    status="failed",
                    target_id=media.id,
                    target_path=media.path,
                    error_message="embedding error",
                    payload={},
                ),
            ]
        )
        db.commit()

        response = get_media_queue(db=db)

    assert response.total == 1
    assert response.items[0].media_status == "embedding_pending"
    assert response.items[0].job_type == "generate_embedding"
    assert response.items[0].job_status == "failed"
    assert response.items[0].error_message == "embedding error"


def test_media_queue_hides_stale_media_error_while_job_is_running() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

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
            status="failed",
            error_message="old connection error",
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
                progress_current=1,
                progress_total=10,
                payload={"stage": "analyze_segments"},
            )
        )
        db.commit()

        response = get_media_queue(db=db)

    assert response.total == 1
    assert response.items[0].job_status == "running"
    assert response.items[0].error_message is None


def test_generate_ai_records_queues_metadata_done_and_reanalysis_media() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)

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
        image_media = MediaFile(
            path="F:/Photos/image.jpg",
            normalized_path="f:/photos/image.jpg",
            root_path="f:/photos",
            parent_dir="f:/photos",
            media_type="image",
            status="metadata_done",
            folder_rule=rule,
        )
        video_media = MediaFile(
            path="F:/Photos/video.mp4",
            normalized_path="f:/photos/video.mp4",
            root_path="f:/photos",
            parent_dir="f:/photos",
            media_type="video",
            status="needs_reanalysis",
            error_message="stale analysis",
            folder_rule=rule,
        )
        done_media = MediaFile(
            path="F:/Photos/done.jpg",
            normalized_path="f:/photos/done.jpg",
            root_path="f:/photos",
            parent_dir="f:/photos",
            media_type="image",
            status="done",
            folder_rule=rule,
        )
        pending_media = MediaFile(
            path="F:/Photos/pending.jpg",
            normalized_path="f:/photos/pending.jpg",
            root_path="f:/photos",
            parent_dir="f:/photos",
            media_type="image",
            status="pending",
            folder_rule=rule,
        )
        active_media = MediaFile(
            path="F:/Photos/active.jpg",
            normalized_path="f:/photos/active.jpg",
            root_path="f:/photos",
            parent_dir="f:/photos",
            media_type="image",
            status="metadata_done",
            folder_rule=rule,
        )
        outside_media = MediaFile(
            path="F:/Other/outside.jpg",
            normalized_path="f:/other/outside.jpg",
            root_path="f:/other",
            parent_dir="f:/other",
            media_type="image",
            status="metadata_done",
        )
        db.add_all([rule, image_media, video_media, done_media, pending_media, active_media, outside_media])
        db.flush()
        db.add(
            Job(
                job_type="analyze_image",
                status="queued",
                target_id=active_media.id,
                target_path=active_media.path,
                payload={},
            )
        )
        db.commit()

        jobs = generate_ai_records(GenerateAiRecordsRequest(directory_rule_id=rule.id), db=db)
        db.refresh(video_media)

    jobs_by_target = {job.target_id: job.job_type for job in jobs}
    assert jobs_by_target == {
        image_media.id: "analyze_image",
        video_media.id: "analyze_video",
    }
    assert video_media.error_message is None
