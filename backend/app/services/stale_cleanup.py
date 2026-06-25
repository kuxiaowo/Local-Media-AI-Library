from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.models.db_models import Job, MediaFile


def cleanup_stale_media(db: Session, job: Job | None = None) -> dict[str, int]:
    media_files = list(
        db.scalars(select(MediaFile).options(joinedload(MediaFile.folder_rule))).all()
    )
    total = len(media_files)
    deleted = 0

    if job is not None:
        job.progress_total = total
        job.progress_current = 0
        db.flush()

    for index, media in enumerate(media_files, start=1):
        if _is_stale(media):
            _delete_thumbnail_cache(media.thumbnail_path)
            _delete_video_frame_cache(str(media.id))
            db.delete(media)
            deleted += 1

        if job is not None:
            job.progress_current = index

        if index % 100 == 0:
            db.flush()

    if job is not None:
        job.payload = {**(job.payload or {}), "checked": total, "deleted": deleted}
    db.flush()
    return {"checked": total, "deleted": deleted}


def _is_stale(media: MediaFile) -> bool:
    if not Path(media.path).exists():
        return True

    if media.folder_rule is not None and not Path(media.folder_rule.path).exists():
        return True

    if media.root_path and not Path(media.root_path).exists():
        return True

    return False


def _delete_thumbnail_cache(thumbnail_path: str | None) -> None:
    if not thumbnail_path:
        return

    settings = get_settings()
    try:
        thumbnail = Path(thumbnail_path).resolve()
        thumbnail_dir = settings.thumbnail_dir.resolve()
        thumbnail.relative_to(thumbnail_dir)
    except (OSError, ValueError):
        return

    try:
        thumbnail.unlink(missing_ok=True)
    except OSError:
        return


def _delete_video_frame_cache(media_id: str) -> None:
    settings = get_settings()
    try:
        frame_dir = (settings.frame_cache_dir / media_id).resolve()
        frame_root = settings.frame_cache_dir.resolve()
        frame_dir.relative_to(frame_root)
    except (OSError, ValueError):
        return

    if not frame_dir.exists():
        return

    try:
        shutil.rmtree(frame_dir)
    except OSError:
        return
