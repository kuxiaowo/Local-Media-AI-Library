from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import or_

from app.core.path_utils import normalize_path
from app.models.db_models import MediaFile


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "none", "null", "unknown", "未知"}:
        return ""
    return text


def clip_text(text: str | None, max_chars: int) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def clamp_score(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return round(max(0.0, min(1.0, number)), 6)


def parse_uuid_list(values: object) -> list[uuid.UUID]:
    if not isinstance(values, list):
        return []
    parsed: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for value in values:
        try:
            media_id = uuid.UUID(str(value))
        except (TypeError, ValueError):
            continue
        if media_id not in seen:
            parsed.append(media_id)
            seen.add(media_id)
    return parsed


def parse_datetime(value: object, *, end_of_day: bool = False) -> datetime | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            parsed_date = date.fromisoformat(text)
            return datetime.combine(parsed_date, time.max if end_of_day else time.min)
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def recent_range(today: str, days: int) -> tuple[datetime | None, datetime | None]:
    try:
        today_date = date.fromisoformat(today)
    except ValueError:
        return None, None
    start = today_date - timedelta(days=max(1, days) - 1)
    return datetime.combine(start, time.min), datetime.combine(today_date, time.max)


def directory_filter(directory_path: str):
    normalized = normalize_path(directory_path)
    return or_(
        MediaFile.parent_dir == normalized,
        MediaFile.parent_dir.like(f"{escape_like(normalized)}/%", escape="\\"),
        MediaFile.root_path == normalized,
    )


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def directory_name(path: str) -> str:
    normalized = path.replace("\\", "/").rstrip("/")
    if not normalized:
        return path
    if re.fullmatch(r"[A-Za-z]:", normalized):
        return normalized.upper()
    return normalized.rsplit("/", 1)[-1] or normalized


def path_matches_directory(path: str | None, directory_path: str) -> bool:
    if not path:
        return False
    normalized = normalize_path(path)
    directory = normalize_path(directory_path)
    return normalized == directory or normalized.startswith(directory + "/")


def jsonish(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return str(value)

