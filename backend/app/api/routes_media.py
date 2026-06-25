from __future__ import annotations

import os
import re
import subprocess
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.path_utils import normalize_path
from app.config import get_settings
from app.database import get_db
from app.models.db_models import DirectoryRule, MediaFile, VideoFrameSummary
from app.models.schemas import MediaDetailRead, MediaDirectoryRead, MediaListResponse
from app.services.job_service import create_job
from app.services.media_visibility import is_media_visible, visible_media_filter

router = APIRouter(prefix="/media", tags=["media"])


@router.get("", response_model=MediaListResponse)
def list_media(
    offset: int = 0,
    limit: int = 60,
    media_type: str = "any",
    status: str = "any",
    directory_path: str | None = None,
    db: Session = Depends(get_db),
) -> MediaListResponse:
    stmt = select(MediaFile).options(joinedload(MediaFile.ai_summary)).order_by(
        MediaFile.captured_at.is_(None), MediaFile.captured_at.desc(), MediaFile.created_at.desc()
    )
    count_stmt = select(func.count(MediaFile.id))
    visibility_filter = visible_media_filter(db)
    stmt = stmt.where(visibility_filter)
    count_stmt = count_stmt.where(visibility_filter)
    if media_type != "any":
        stmt = stmt.where(MediaFile.media_type == media_type)
        count_stmt = count_stmt.where(MediaFile.media_type == media_type)
    if status != "any":
        stmt = stmt.where(MediaFile.status == status)
        count_stmt = count_stmt.where(MediaFile.status == status)
    if directory_path:
        directory_filter = _directory_filter(directory_path)
        stmt = stmt.where(directory_filter)
        count_stmt = count_stmt.where(directory_filter)
    total = db.scalar(count_stmt) or 0
    items = list(db.scalars(stmt.offset(offset).limit(min(limit, 200))).unique().all())
    return MediaListResponse(items=items, total=total, offset=offset, limit=limit)


@router.get("/directories", response_model=list[MediaDirectoryRead])
def list_media_directories(db: Session = Depends(get_db)) -> list[MediaDirectoryRead]:
    directories: dict[str, MediaDirectoryRead] = {}

    visibility_filter = visible_media_filter(db)

    for rule in db.scalars(select(DirectoryRule).where(DirectoryRule.enabled)).all():
        directories[rule.normalized_path] = MediaDirectoryRead(
            path=rule.normalized_path,
            display_path=rule.path,
            name=_directory_name(rule.path),
            direct_media_count=0,
        )

    rows = db.execute(
        select(MediaFile.parent_dir, func.count(MediaFile.id), func.min(MediaFile.path))
        .where(visibility_filter, MediaFile.parent_dir.is_not(None))
        .group_by(MediaFile.parent_dir)
    ).all()
    for parent_dir, media_count, sample_path in rows:
        if not parent_dir:
            continue
        display_path = _display_parent_path(sample_path, parent_dir)
        existing = directories.get(parent_dir)
        if existing is None:
            directories[parent_dir] = MediaDirectoryRead(
                path=parent_dir,
                display_path=display_path,
                name=_directory_name(display_path),
                direct_media_count=int(media_count),
            )
        else:
            existing.direct_media_count = int(media_count)

    return sorted(directories.values(), key=lambda item: item.path)


@router.get("/{media_id}", response_model=MediaDetailRead)
def get_media(media_id: uuid.UUID, db: Session = Depends(get_db)) -> MediaFile:
    media = db.scalar(
        select(MediaFile)
        .options(
            joinedload(MediaFile.ai_summary),
            selectinload(MediaFile.video_frames),
            selectinload(MediaFile.video_segments),
        )
        .where(MediaFile.id == media_id)
    )
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return media


@router.get("/{media_id}/thumbnail")
def get_thumbnail(media_id: uuid.UUID, db: Session = Depends(get_db)) -> FileResponse:
    media = db.get(MediaFile, media_id)
    if media is None or not media.thumbnail_path:
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    path = Path(media.thumbnail_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail file not found")
    return FileResponse(path)


@router.get("/{media_id}/frames/{frame_id}")
def get_video_frame(media_id: uuid.UUID, frame_id: uuid.UUID, db: Session = Depends(get_db)) -> FileResponse:
    frame = db.get(VideoFrameSummary, frame_id)
    if frame is None or frame.media_id != media_id:
        raise HTTPException(status_code=404, detail="Video frame not found")
    media = db.get(MediaFile, media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    _assert_media_is_under_known_root(db, media)

    path = Path(frame.frame_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Video frame file not found")
    try:
        path.resolve().relative_to(get_settings().frame_cache_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Video frame is outside the frame cache")
    return FileResponse(path)


@router.get("/{media_id}/preview")
def get_preview(media_id: uuid.UUID, db: Session = Depends(get_db)) -> FileResponse:
    media = db.get(MediaFile, media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    _assert_media_is_under_known_root(db, media)
    path = Path(media.path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Media file not found")
    return FileResponse(path)


@router.post("/{media_id}/reanalyze")
def reanalyze(media_id: uuid.UUID, db: Session = Depends(get_db)):
    media = db.get(MediaFile, media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return create_job(db, job_type="reanalyze_media", target_id=media.id, target_path=media.path)


@router.post("/{media_id}/open-location")
def open_location(media_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, bool]:
    media = db.get(MediaFile, media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    _assert_media_is_under_known_root(db, media)
    path = Path(media.path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Media file not found")
    if os.name == "nt":
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])
    return {"ok": True}


def _assert_media_is_under_known_root(db: Session, media: MediaFile) -> None:
    if not is_media_visible(db, media):
        raise HTTPException(status_code=403, detail="Media is not under an enabled library root")


def _directory_filter(directory_path: str):
    normalized = normalize_path(directory_path)
    return or_(
        MediaFile.parent_dir == normalized,
        MediaFile.parent_dir.like(f"{_escape_like(normalized)}/%", escape="\\"),
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _display_parent_path(sample_path: str | None, fallback: str) -> str:
    if not sample_path:
        return fallback
    normalized = sample_path.replace("\\", "/").rstrip("/")
    if "/" not in normalized:
        return fallback
    return normalized.rsplit("/", 1)[0]


def _directory_name(path: str) -> str:
    normalized = path.replace("\\", "/").rstrip("/")
    if not normalized:
        return path
    if re.fullmatch(r"[A-Za-z]:", normalized):
        return normalized.upper()
    return normalized.rsplit("/", 1)[-1] or normalized
