from __future__ import annotations

from collections.abc import Iterable


def _join(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(str(v) for v in value.values())
    if isinstance(value, Iterable):
        return " ".join(str(v) for v in value)
    return str(value)


def build_searchable_text(
    *,
    title: str | None,
    captured_at: object,
    media_type: str,
    parent_dir: str | None,
    short_summary: str | None,
    detailed_summary: str | None,
    scene: str | None,
    objects: object,
    people: object,
    actions: object,
    text_visible: object,
    location_guess: str | None,
    mood: str | None,
    search_keywords: object,
) -> str:
    lines = [
        f"Title: {title or ''}",
        f"Time: {captured_at or ''}",
        f"Media type: {media_type}",
        f"Directory: {parent_dir or ''}",
        f"Short summary: {short_summary or ''}",
        f"Detailed summary: {detailed_summary or ''}",
        f"Scene: {scene or ''}",
        f"Objects: {_join(objects)}",
        f"People: {_join(people)}",
        f"Actions: {_join(actions)}",
        f"Visible text: {_join(text_visible)}",
        f"Location guess: {location_guess or ''}",
        f"Mood: {mood or ''}",
        f"Keywords: {_join(search_keywords)}",
    ]
    return "\n".join(lines)
