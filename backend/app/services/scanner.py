from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.path_utils import normalize_path, parent_dir, path_has_prefix
from app.models.db_models import DirectoryRule, MediaFile
from app.services.job_service import create_job
from app.services.media_detector import detect_media_type, image_support_status


def scan_directory(
    db: Session,
    rule: DirectoryRule,
    job_id: object | None = None,
    mode: str = "incremental",
) -> int:
    if mode not in {"incremental", "full"}:
        raise ValueError(f"Unknown scan mode: {mode}")

    root = Path(rule.path)
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Directory does not exist: {rule.path}")

    scan_started_at = datetime.now(timezone.utc)
    iterator = root.rglob("*") if rule.recursive else root.glob("*")
    discovered = 0
    seen_paths: set[str] = set()

    for path in iterator:
        if not path.is_file():
            continue
        media_type = detect_media_type(path)
        if media_type != "image":
            continue

        normalized = normalize_path(path)
        seen_paths.add(normalized)
        stat = path.stat()
        discovered += 1

        media = db.scalar(select(MediaFile).where(MediaFile.normalized_path == normalized))
        created = media is None
        was_missing = media is not None and media.status == "missing"
        if media is None:
            media = MediaFile(
                path=str(path),
                normalized_path=normalized,
                root_path=rule.normalized_path,
                parent_dir=parent_dir(str(path)),
                media_type=media_type,
                file_size=stat.st_size,
                file_modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                last_seen_at=scan_started_at,
                status="pending",
            )
            db.add(media)
            db.flush()
        else:
            media.path = str(path)
            media.root_path = rule.normalized_path
            media.parent_dir = parent_dir(str(path))
            media.file_size = stat.st_size
            media.file_modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            media.last_seen_at = scan_started_at
            if mode == "full":
                media.status = "pending"
                media.error_message = None
            elif was_missing:
                media.status = "pending"
                media.error_message = None

        if image_support_status(path) == "recognized_unsupported":
            media.status = "failed"
            media.error_message = "HEIC/HEIF is recognized but not supported in the MVP"
        elif mode == "full" or created or was_missing:
            create_job(db, job_type="extract_metadata", target_id=media.id, target_path=media.path)

    existing = db.scalars(
        select(MediaFile).where(MediaFile.root_path == rule.normalized_path, MediaFile.status != "missing")
    ).all()
    for media in existing:
        if path_has_prefix(media.normalized_path, rule.normalized_path) and media.normalized_path not in seen_paths:
            media.status = "missing"
            media.error_message = "File was not found during the latest scan"

    db.commit()
    return discovered
