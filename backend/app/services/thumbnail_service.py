from __future__ import annotations

from pathlib import Path
from uuid import UUID

from PIL import Image, ImageOps

from app.config import get_settings


def generate_thumbnail(image_path: str, media_id: UUID, size: tuple[int, int] = (512, 512)) -> str:
    settings = get_settings()
    settings.thumbnail_dir.mkdir(parents=True, exist_ok=True)
    output_path = settings.thumbnail_dir / f"{media_id}.jpg"
    with Image.open(image_path) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail(size)
        rgb = image.convert("RGB")
        rgb.save(output_path, "JPEG", quality=85, optimize=True)
    return str(Path(output_path).resolve())
