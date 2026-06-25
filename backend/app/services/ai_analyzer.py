from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.db_models import MediaAiSummary, MediaFile
from app.prompts.image_analysis import IMAGE_ANALYSIS_SCHEMA
from app.services.ollama_client import OllamaClient
from app.services.searchable_text import build_searchable_text


async def analyze_image(db: Session, media: MediaFile, ollama: OllamaClient) -> MediaAiSummary:
    if media.folder_rule is None:
        media.status = "failed"
        media.error_message = "No enabled directory rule matched this media file"
        raise RuntimeError(media.error_message)

    media.status = "analyzing"
    db.commit()
    raw = await ollama.generate_vision_json(
        model=media.folder_rule.vision_model,
        image_path=media.path,
        schema=IMAGE_ANALYSIS_SCHEMA,
        custom_analysis_prompt=media.folder_rule.custom_analysis_prompt,
        background_context=media.folder_rule.background_context,
    )
    raw = _normalize_image_analysis(raw)
    searchable_text = build_searchable_text(
        title=raw.get("title"),
        captured_at=media.captured_at,
        media_type=media.media_type,
        parent_dir=media.parent_dir,
        short_summary=raw.get("short_summary"),
        detailed_summary=raw.get("detailed_summary"),
        scene=raw.get("scene"),
        objects=raw.get("objects"),
        people=raw.get("people"),
        actions=raw.get("actions"),
        text_visible=raw.get("text_visible"),
        location_guess=raw.get("location_guess"),
        mood=raw.get("mood"),
        search_keywords=raw.get("search_keywords"),
    )
    summary = db.get(MediaAiSummary, media.id)
    if summary is None:
        summary = MediaAiSummary(media_id=media.id, model_used=media.folder_rule.vision_model)
    summary.model_used = media.folder_rule.vision_model
    summary.title = raw.get("title")
    summary.short_summary = raw.get("short_summary")
    summary.detailed_summary = raw.get("detailed_summary")
    summary.scene = raw.get("scene")
    summary.objects = raw.get("objects") or []
    summary.people = raw.get("people") or []
    summary.actions = raw.get("actions") or []
    summary.text_visible = raw.get("text_visible") or []
    summary.location_guess = raw.get("location_guess")
    summary.time_clues = raw.get("time_clues")
    summary.mood = raw.get("mood")
    summary.search_keywords = raw.get("search_keywords") or []
    summary.searchable_text = searchable_text
    summary.raw_json = raw
    summary.confidence = raw.get("confidence")
    db.add(summary)
    media.status = "done"
    media.error_message = None
    db.add(media)
    db.commit()
    db.refresh(summary)
    return summary


def _normalize_image_analysis(raw: dict) -> dict:
    short_summary = _clean_text(raw.get("short_summary"))
    detailed_summary = _clean_text(raw.get("detailed_summary"))
    scene = _clean_text(raw.get("scene"))
    title = _clean_text(raw.get("title"))

    if not title:
        title = _title_from_summary(short_summary or detailed_summary or scene)
    if not title:
        title = "未命名图片"

    raw["title"] = title
    raw["short_summary"] = short_summary or title
    raw["detailed_summary"] = detailed_summary or short_summary or title
    raw["scene"] = scene or "未知场景"
    raw["objects"] = _list_value(raw.get("objects"))
    raw["people"] = _list_value(raw.get("people"))
    raw["actions"] = _list_value(raw.get("actions"))
    raw["text_visible"] = _list_value(raw.get("text_visible"))
    raw["search_keywords"] = _list_value(raw.get("search_keywords")) or [title]
    raw["location_guess"] = _clean_text(raw.get("location_guess")) or "unknown"
    raw["time_clues"] = _clean_text(raw.get("time_clues")) or "unknown"
    raw["mood"] = _clean_text(raw.get("mood")) or "unknown"
    raw["confidence"] = raw.get("confidence") if raw.get("confidence") in {"high", "medium", "low"} else "medium"
    return raw


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "none", "null", "unknown", "无", "未知"}:
        return ""
    return text


def _list_value(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, dict):
        return [str(item).strip() for item in value.values() if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _title_from_summary(text: str) -> str:
    text = text.strip().replace("\n", " ")
    if not text:
        return ""
    for separator in ("。", "，", ",", ".", "；", ";"):
        if separator in text:
            text = text.split(separator, 1)[0]
            break
    return text[:24]
