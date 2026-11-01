from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.api.routes_directory_rules import delete_rule
from app.core.path_utils import normalize_path
from app.database import Base
from app.models.db_models import DirectoryRule, MediaFile
from app.services.stale_cleanup import cleanup_stale_media


def test_cleanup_deletes_media_that_is_no_longer_under_any_directory_rule(tmp_path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    root = tmp_path / "Library"
    root.mkdir()
    image = root / "image.jpg"
    image.write_bytes(b"content")

    with SessionLocal() as db:
        rule = _rule(root)
        media = MediaFile(
            path=str(image),
            normalized_path=normalize_path(image),
            root_path=rule.normalized_path,
            parent_dir=normalize_path(root),
            media_type="image",
            status="done",
            folder_rule=rule,
        )
        db.add_all([rule, media])
        db.commit()

        delete_rule(rule.id, db=db)
        result = cleanup_stale_media(db)
        remaining_media = list(db.scalars(select(MediaFile)).all())

    assert image.exists()
    assert result == {"checked": 1, "deleted": 1}
    assert remaining_media == []


def test_cleanup_keeps_media_under_disabled_directory_rule(tmp_path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    root = tmp_path / "Library"
    root.mkdir()
    image = root / "image.jpg"
    image.write_bytes(b"content")

    with SessionLocal() as db:
        rule = _rule(root, enabled=False)
        media = MediaFile(
            path=str(image),
            normalized_path=normalize_path(image),
            root_path=rule.normalized_path,
            parent_dir=normalize_path(root),
            media_type="image",
            status="done",
            folder_rule=rule,
        )
        db.add_all([rule, media])
        db.commit()

        result = cleanup_stale_media(db)
        remaining_media = list(db.scalars(select(MediaFile)).all())

    assert result == {"checked": 1, "deleted": 0}
    assert len(remaining_media) == 1
    assert remaining_media[0].normalized_path == normalize_path(image)


def _rule(root, *, enabled: bool = True) -> DirectoryRule:
    return DirectoryRule(
        path=str(root),
        normalized_path=normalize_path(root),
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
        enabled=enabled,
    )
