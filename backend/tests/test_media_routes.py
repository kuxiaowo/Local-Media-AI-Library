from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes_media import list_media, update_media_background_context
from app.database import Base
from app.models.db_models import DirectoryRule, MediaFile
from app.models.schemas import MediaBackgroundContextUpdate


def test_update_media_background_context_marks_done_media_for_reanalysis() -> None:
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
        media = MediaFile(
            path="F:/Photos/image.jpg",
            normalized_path="f:/photos/image.jpg",
            root_path="f:/photos",
            parent_dir="f:/photos",
            media_type="image",
            status="done",
            folder_rule=rule,
        )
        db.add_all([rule, media])
        db.commit()
        db.refresh(media)

        updated = update_media_background_context(
            media.id,
            MediaBackgroundContextUpdate(
                background_context="只对这张图生效的背景",
            ),
            db=db,
        )

    assert updated.background_context == "只对这张图生效的背景"
    assert updated.status == "needs_reanalysis"
    assert updated.error_message == (
        "Media background prompt changed; previous analysis is retained until reanalysis runs"
    )


def test_update_media_background_context_marks_embedding_pending_media_for_reanalysis() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        media = MediaFile(
            path="F:/Photos/image.jpg",
            normalized_path="f:/photos/image.jpg",
            root_path="f:/photos",
            parent_dir="f:/photos",
            media_type="image",
            status="embedding_pending",
        )
        db.add(media)
        db.commit()
        db.refresh(media)

        updated = update_media_background_context(
            media.id,
            MediaBackgroundContextUpdate(background_context="新的背景"),
            db=db,
        )

    assert updated.status == "needs_reanalysis"


def test_list_media_orders_by_directory_then_filename() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        rule = DirectoryRule(
            path="F:/Library",
            normalized_path="f:/library",
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
        media_files = [
            MediaFile(
                path="F:/Library/B/c/01.jpg",
                normalized_path="f:/library/b/c/01.jpg",
                root_path="f:/library",
                parent_dir="f:/library/b/c",
                media_type="image",
                status="metadata_done",
                folder_rule=rule,
            ),
            MediaFile(
                path="F:/Library/A/b/02.jpg",
                normalized_path="f:/library/a/b/02.jpg",
                root_path="f:/library",
                parent_dir="f:/library/a/b",
                media_type="image",
                status="metadata_done",
                folder_rule=rule,
            ),
            MediaFile(
                path="F:/Library/A/a/02.jpg",
                normalized_path="f:/library/a/a/02.jpg",
                root_path="f:/library",
                parent_dir="f:/library/a/a",
                media_type="image",
                status="metadata_done",
                folder_rule=rule,
            ),
            MediaFile(
                path="F:/Library/A/a/01.jpg",
                normalized_path="f:/library/a/a/01.jpg",
                root_path="f:/library",
                parent_dir="f:/library/a/a",
                media_type="image",
                status="metadata_done",
                folder_rule=rule,
            ),
        ]
        db.add_all([rule, *media_files])
        db.commit()

        response = list_media(db=db)

    assert [item.normalized_path for item in response.items] == [
        "f:/library/a/a/01.jpg",
        "f:/library/a/a/02.jpg",
        "f:/library/a/b/02.jpg",
        "f:/library/b/c/01.jpg",
    ]
