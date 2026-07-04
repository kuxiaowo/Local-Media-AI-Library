from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.path_utils import normalize_path
from app.database import Base
from app.models.db_models import DirectoryRule, EmbeddingProfile, Job, MediaAiSummary, MediaEmbedding, MediaFile
from app.services.scanner import scan_directory


def test_scan_directory_records_run_ai_false_on_metadata_jobs(tmp_path) -> None:
    root = tmp_path / "Photos"
    root.mkdir()
    image = root / "image.jpg"
    image.write_bytes(b"placeholder")

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        rule = DirectoryRule(
            path=str(root),
            normalized_path=normalize_path(str(root)),
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
        db.add(rule)
        db.commit()

        discovered = scan_directory(db, rule, mode="incremental", run_ai=False)
        jobs = list(db.scalars(select(Job)).all())

    assert discovered == 1
    assert len(jobs) == 1
    assert jobs[0].job_type == "extract_metadata"
    assert jobs[0].payload == {"run_ai": False}


def test_scan_directory_skips_disabled_descendant_rule(tmp_path) -> None:
    root = tmp_path / "Photos"
    private = root / "Private"
    private.mkdir(parents=True)
    visible = root / "visible.jpg"
    hidden = private / "hidden.jpg"
    visible.write_bytes(b"placeholder")
    hidden.write_bytes(b"placeholder")

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        parent_rule = DirectoryRule(
            path=str(root),
            normalized_path=normalize_path(str(root)),
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
        disabled_child_rule = DirectoryRule(
            path=str(private),
            normalized_path=normalize_path(str(private)),
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
            enabled=False,
        )
        existing_hidden_media = MediaFile(
            path=str(hidden),
            normalized_path=normalize_path(str(hidden)),
            root_path=parent_rule.normalized_path,
            parent_dir=normalize_path(str(private)),
            media_type="image",
            status="done",
            folder_rule=parent_rule,
        )
        db.add_all([parent_rule, disabled_child_rule, existing_hidden_media])
        db.commit()

        discovered = scan_directory(db, parent_rule, mode="incremental", run_ai=False)
        media_paths = list(db.scalars(select(MediaFile.normalized_path)).all())
        db.refresh(existing_hidden_media)

    assert discovered == 1
    assert normalize_path(str(visible)) in media_paths
    assert existing_hidden_media.status == "done"


def test_incremental_scan_does_not_requeue_unchanged_file(tmp_path) -> None:
    root = tmp_path / "Photos"
    root.mkdir()
    image = root / "image.jpg"
    image.write_bytes(b"same")
    stat = image.stat()

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)

    with SessionLocal() as db:
        rule = _rule(root)
        media = MediaFile(
            path=str(image),
            normalized_path=normalize_path(str(image)),
            root_path=rule.normalized_path,
            parent_dir=normalize_path(str(root)),
            media_type="image",
            file_size=stat.st_size,
            file_modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            status="done",
            folder_rule=rule,
        )
        db.add_all([rule, media])
        db.commit()

        discovered = scan_directory(db, rule, mode="incremental", run_ai=False)
        jobs = list(db.scalars(select(Job)).all())
        db.refresh(media)

    assert discovered == 1
    assert jobs == []
    assert media.status == "done"


def test_incremental_scan_requeues_missing_file_when_seen_again(tmp_path) -> None:
    root = tmp_path / "Photos"
    root.mkdir()
    image = root / "restored.jpg"
    image.write_bytes(b"restored")

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)

    with SessionLocal() as db:
        rule = _rule(root)
        media = MediaFile(
            path=str(image),
            normalized_path=normalize_path(str(image)),
            root_path=rule.normalized_path,
            parent_dir=normalize_path(str(root)),
            media_type="image",
            status="missing",
            error_message="old missing",
            folder_rule=rule,
        )
        db.add_all([rule, media])
        db.commit()

        discovered = scan_directory(db, rule, mode="incremental", run_ai=False)
        jobs = list(db.scalars(select(Job)).all())
        db.refresh(media)

    assert discovered == 1
    assert media.status == "pending"
    assert media.error_message is None
    assert len(jobs) == 1
    assert jobs[0].job_type == "extract_metadata"


def test_incremental_scan_requeues_changed_file_and_clears_stale_ai_data(tmp_path) -> None:
    root = tmp_path / "Photos"
    root.mkdir()
    image = root / "changed.jpg"
    image.write_bytes(b"new content")

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)

    with SessionLocal() as db:
        rule = _rule(root)
        media = MediaFile(
            path=str(image),
            normalized_path=normalize_path(str(image)),
            root_path=rule.normalized_path,
            parent_dir=normalize_path(str(root)),
            media_type="image",
            file_size=1,
            file_modified_at=datetime.fromtimestamp(1, tz=timezone.utc),
            file_hash="old-hash",
            thumbnail_path=str(tmp_path / "old-thumb.jpg"),
            status="done",
            folder_rule=rule,
        )
        profile = EmbeddingProfile(model_name="embed", dimension=3)
        db.add_all([rule, media, profile])
        db.flush()
        db.add_all(
            [
                MediaAiSummary(
                    media_id=media.id,
                    model_used="vision",
                    searchable_text="old summary",
                ),
                MediaEmbedding(
                    media_id=media.id,
                    profile_id=profile.id,
                    embedding=[0.1, 0.2, 0.3],
                    embedded_text="old summary",
                ),
            ]
        )
        db.commit()

        discovered = scan_directory(db, rule, mode="incremental", run_ai=False)
        jobs = list(db.scalars(select(Job)).all())
        db.refresh(media)

        summary_count = db.scalar(select(func.count(MediaAiSummary.media_id)))
        embedding_count = db.scalar(select(func.count(MediaEmbedding.id)))

    assert discovered == 1
    assert media.status == "pending"
    assert media.error_message is None
    assert media.file_hash is None
    assert media.thumbnail_path is None
    assert summary_count == 0
    assert embedding_count == 0
    assert len(jobs) == 1
    assert jobs[0].target_id == media.id


def test_full_scan_does_not_duplicate_active_extract_metadata_job(tmp_path) -> None:
    root = tmp_path / "Photos"
    root.mkdir()
    image = root / "image.jpg"
    image.write_bytes(b"content")
    stat = image.stat()

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)

    with SessionLocal() as db:
        rule = _rule(root)
        media = MediaFile(
            path=str(image),
            normalized_path=normalize_path(str(image)),
            root_path=rule.normalized_path,
            parent_dir=normalize_path(str(root)),
            media_type="image",
            file_size=stat.st_size,
            file_modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            status="done",
            folder_rule=rule,
        )
        db.add_all([rule, media])
        db.flush()
        db.add(Job(job_type="extract_metadata", status="queued", target_id=media.id, target_path=media.path, payload={}))
        db.commit()

        discovered = scan_directory(db, rule, mode="full", run_ai=False)
        job_count = db.scalar(select(func.count(Job.id)))
        db.refresh(media)

    assert discovered == 1
    assert media.status == "pending"
    assert job_count == 1


def _rule(root) -> DirectoryRule:
    return DirectoryRule(
        path=str(root),
        normalized_path=normalize_path(str(root)),
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
