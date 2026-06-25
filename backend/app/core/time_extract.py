from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import exifread


@dataclass(frozen=True)
class CapturedTime:
    captured_at: datetime
    source: str
    confidence: str


def _from_timestamp(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _parse_exif_datetime(value: object) -> datetime | None:
    text = str(value).strip()
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def extract_image_captured_time(path: str | Path) -> CapturedTime:
    file_path = Path(path)
    try:
        with file_path.open("rb") as handle:
            tags = exifread.process_file(handle, details=False, stop_tag="EXIF DateTimeOriginal")
        for tag_name, source in (
            ("EXIF DateTimeOriginal", "exif_datetime_original"),
            ("Image DateTime", "exif_modify_date"),
            ("EXIF DateTimeDigitized", "exif_create_date"),
        ):
            if tag_name in tags:
                parsed = _parse_exif_datetime(tags[tag_name])
                if parsed is not None:
                    return CapturedTime(parsed, source, "high")
    except Exception:
        pass

    stat = file_path.stat()
    created = getattr(stat, "st_birthtime", None)
    if created is not None:
        return CapturedTime(_from_timestamp(created), "file_ctime", "low")
    return CapturedTime(_from_timestamp(stat.st_mtime), "file_mtime", "low")
