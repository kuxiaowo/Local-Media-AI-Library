from __future__ import annotations

from pathlib import Path

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif"}
RECOGNIZED_UNSUPPORTED_IMAGE_EXTENSIONS = {".heic", ".heif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}


def image_support_status(path: str | Path) -> str | None:
    suffix = Path(path).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "supported"
    if suffix in RECOGNIZED_UNSUPPORTED_IMAGE_EXTENSIONS:
        return "recognized_unsupported"
    return None


def detect_media_type(path: str | Path) -> str | None:
    if image_support_status(path) is not None:
        return "image"
    if Path(path).suffix.lower() in VIDEO_EXTENSIONS:
        return "video"
    return None
