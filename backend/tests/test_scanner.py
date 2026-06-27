from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.path_utils import normalize_path
from app.database import Base
from app.models.db_models import DirectoryRule, Job, MediaFile
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
