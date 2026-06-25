from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps
from sqlalchemy.orm import Session

from app.core.hashing import sha256_file
from app.core.time_extract import extract_image_captured_time
from app.models.db_models import MediaFile
from app.services.media_detector import image_support_status
from app.services.thumbnail_service import generate_thumbnail


def extract_image_metadata(db: Session, media: MediaFile) -> MediaFile:
    path = Path(media.path)
    if not path.exists():
        media.status = "missing"
        media.error_message = "File no longer exists"
        return media

    if image_support_status(path) == "recognized_unsupported":
        media.status = "failed"
        media.error_message = "HEIC/HEIF is recognized but not supported in the MVP"
        return media

    stat = path.stat()
    media.file_size = stat.st_size
    media.file_modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    ctime = getattr(stat, "st_birthtime", stat.st_ctime)
    media.file_created_at = datetime.fromtimestamp(ctime, tz=timezone.utc)
    media.file_hash = sha256_file(path)

    captured = extract_image_captured_time(path)
    media.captured_at = captured.captured_at
    media.captured_at_source = captured.source
    media.captured_at_confidence = captured.confidence

    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        media.width, media.height = image.size

    media.thumbnail_path = generate_thumbnail(str(path), media.id)
    media.status = "metadata_done"
    media.error_message = None
    db.add(media)
    return media
