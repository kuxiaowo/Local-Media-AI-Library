from __future__ import annotations

import json

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.db_models import MediaAiSummary, MediaFile, VideoFrameSummary, VideoSegmentSummary
from app.prompts.image_analysis import IMAGE_ANALYSIS_SCHEMA
from app.prompts.video_analysis import (
    VIDEO_FINAL_SUMMARY_SCHEMA,
    VIDEO_FINAL_SUMMARY_SYSTEM_PROMPT,
    VIDEO_SEGMENT_ANALYSIS_SCHEMA,
    VIDEO_SEGMENT_ANALYSIS_SYSTEM_PROMPT,
    build_video_final_summary_user_prompt,
    build_video_segment_user_prompt,
)
from app.services.ollama_client import OllamaClient
from app.services.prompt_settings import (
    get_default_video_final_summary_system_prompt,
    get_default_video_final_summary_prompt,
    get_default_video_segment_system_prompt,
    get_default_video_segment_prompt,
)
from app.services.searchable_text import build_searchable_text
from app.services.video_frame_extractor import ExtractedFrame, batch_frames, extract_video_frames


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
        background_context_prompt=media.folder_rule.background_context_prompt,
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


async def analyze_video(db: Session, media: MediaFile, ollama: OllamaClient) -> MediaAiSummary:
    if media.folder_rule is None:
        media.status = "failed"
        media.error_message = "No enabled directory rule matched this media file"
        raise RuntimeError(media.error_message)

    rule = media.folder_rule
    media.status = "analyzing"
    db.commit()

    frames = extract_video_frames(
        media.path,
        media.id,
        strategy=rule.video_frame_strategy,
        interval_seconds=rule.frame_interval_seconds,
        max_frames=rule.max_frames_per_video,
        max_width=rule.video_frame_max_width,
        max_height=rule.video_frame_max_height,
        duration_seconds=media.duration_seconds,
    )

    frame_batches = batch_frames(frames, rule.video_batch_size)
    if not frame_batches:
        raise RuntimeError("No video frames were extracted")

    db.execute(delete(VideoFrameSummary).where(VideoFrameSummary.media_id == media.id))
    db.execute(delete(VideoSegmentSummary).where(VideoSegmentSummary.media_id == media.id))
    db.commit()

    previous_global_summary = ""
    previous_timeline: list[dict] = []
    saved_segments: list[VideoSegmentSummary] = []
    default_video_segment_prompt = get_default_video_segment_prompt()
    default_video_segment_system_prompt = get_default_video_segment_system_prompt()
    default_video_final_summary_prompt = get_default_video_final_summary_prompt()
    default_video_final_summary_system_prompt = get_default_video_final_summary_system_prompt()

    frame_index = 1
    for segment_index, current_batch in enumerate(frame_batches, start=1):
        frame_infos = _frame_infos(current_batch, start_index=frame_index)
        raw = await ollama.generate_vision_batch_json(
            model=rule.vision_model,
            image_paths=[frame.frame_path for frame in current_batch],
            schema=VIDEO_SEGMENT_ANALYSIS_SCHEMA,
            system_prompt=(
                default_video_segment_system_prompt
                or VIDEO_SEGMENT_ANALYSIS_SYSTEM_PROMPT
            ),
            prompt=build_video_segment_user_prompt(
                previous_global_summary=previous_global_summary,
                previous_timeline=previous_timeline,
                frame_infos=frame_infos,
                background_context=rule.background_context,
                background_context_prompt=rule.background_context_prompt,
                custom_segment_prompt=rule.video_segment_prompt,
                default_segment_prompt=default_video_segment_prompt,
            ),
        )
        raw = _normalize_video_segment_analysis(raw, previous_global_summary, previous_timeline)

        segment = VideoSegmentSummary(
            media_id=media.id,
            segment_index=segment_index,
            start_time_seconds=current_batch[0].timestamp_seconds,
            end_time_seconds=current_batch[-1].timestamp_seconds,
            frame_paths=[frame.frame_path for frame in current_batch],
            current_segment_summary=raw.get("current_segment_summary"),
            current_segment_tags=raw.get("current_segment_tags") or [],
            important_objects=raw.get("important_objects") or [],
            ocr_text=raw.get("ocr_text") or [],
            new_objects_or_scenes=raw.get("new_objects_or_scenes") or [],
            updated_global_summary=raw.get("updated_global_summary"),
            updated_timeline=raw.get("updated_timeline") or [],
            confidence=raw.get("confidence"),
            raw_json=raw,
        )
        db.add(segment)
        db.flush()

        for offset, frame in enumerate(current_batch):
            db.add(
                VideoFrameSummary(
                    media_id=media.id,
                    segment_id=segment.id,
                    frame_index=frame_index + offset,
                    timestamp_seconds=frame.timestamp_seconds,
                    frame_path=frame.frame_path,
                    model_used=rule.vision_model,
                    caption=raw.get("current_segment_summary"),
                    objects=raw.get("important_objects") or [],
                    people=[],
                    actions=raw.get("current_segment_tags") or [],
                    text_visible=raw.get("ocr_text") or [],
                    raw_json={
                        "segment_id": str(segment.id),
                        "segment_index": segment_index,
                        "frame_index": frame_index + offset,
                    },
                )
            )

        db.commit()
        db.refresh(segment)
        saved_segments.append(segment)

        previous_global_summary = raw.get("updated_global_summary") or previous_global_summary
        previous_timeline = raw.get("updated_timeline") or previous_timeline
        frame_index += len(current_batch)

    if not saved_segments:
        raise RuntimeError("No video segment analysis was produced")

    return await _finalize_video_summary(
        db=db,
        media=media,
        rule=rule,
        ollama=ollama,
        saved_segments=saved_segments,
        rolling_global_summary=previous_global_summary,
        rolling_timeline=previous_timeline,
        default_video_final_summary_system_prompt=default_video_final_summary_system_prompt,
        default_video_final_summary_prompt=default_video_final_summary_prompt,
    )

async def _finalize_video_summary(
    *,
    db: Session,
    media: MediaFile,
    rule: object,
    ollama: OllamaClient,
    saved_segments: list[VideoSegmentSummary],
    rolling_global_summary: str,
    rolling_timeline: list[dict],
    default_video_final_summary_system_prompt: str,
    default_video_final_summary_prompt: str,
) -> MediaAiSummary:
    segment_payloads = _segment_payloads(saved_segments)
    raw = await ollama.generate_text_json(
        model=rule.summary_model,
        schema=VIDEO_FINAL_SUMMARY_SCHEMA,
        system_prompt=(
            default_video_final_summary_system_prompt
            or VIDEO_FINAL_SUMMARY_SYSTEM_PROMPT
        ),
        prompt=build_video_final_summary_user_prompt(
            duration_seconds=media.duration_seconds,
            segments=segment_payloads,
            rolling_global_summary=rolling_global_summary,
            rolling_timeline=rolling_timeline,
            background_context=rule.background_context,
            background_context_prompt=rule.background_context_prompt,
            custom_final_prompt=rule.video_final_summary_prompt,
            default_final_prompt=default_video_final_summary_prompt,
        ),
    )
    raw = _normalize_video_final_summary(raw, saved_segments, rolling_global_summary, rolling_timeline)

    searchable_text = build_searchable_text(
        title=raw.get("title"),
        captured_at=media.captured_at,
        media_type=media.media_type,
        parent_dir=media.parent_dir,
        short_summary=raw.get("short_summary"),
        detailed_summary=raw.get("detailed_summary"),
        scene=raw.get("scene"),
        objects=raw.get("objects"),
        people=[],
        actions=raw.get("actions"),
        text_visible=raw.get("text_visible"),
        location_guess="unknown",
        mood="unknown",
        search_keywords=raw.get("search_keywords"),
    )

    summary = db.get(MediaAiSummary, media.id)
    if summary is None:
        summary = MediaAiSummary(media_id=media.id, model_used=rule.summary_model)
    summary.model_used = rule.summary_model
    summary.title = raw.get("title")
    summary.short_summary = raw.get("short_summary")
    summary.detailed_summary = raw.get("detailed_summary")
    summary.scene = raw.get("scene")
    summary.objects = raw.get("objects") or []
    summary.people = []
    summary.actions = raw.get("actions") or []
    summary.text_visible = raw.get("text_visible") or []
    summary.location_guess = "unknown"
    summary.time_clues = json.dumps(raw.get("timeline") or [], ensure_ascii=False)
    summary.mood = "unknown"
    summary.search_keywords = raw.get("search_keywords") or []
    summary.searchable_text = searchable_text
    summary.raw_json = {
        "final_summary": raw,
        "timeline": raw.get("timeline") or [],
        "vision_model": rule.vision_model,
        "summary_model": rule.summary_model,
        "rolling_global_summary": rolling_global_summary,
        "rolling_timeline": rolling_timeline,
        "segment_count": len(saved_segments),
        "segment_summaries": segment_payloads,
    }
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


def _normalize_video_frame_analysis(raw: dict) -> dict:
    caption = _clean_text(raw.get("caption")) or _clean_text(raw.get("short_summary"))
    scene = _clean_text(raw.get("scene"))
    if not caption:
        caption = scene or "视频关键帧"
    raw["caption"] = caption
    raw["scene"] = scene or "未知场景"
    raw["objects"] = _list_value(raw.get("objects"))
    raw["people"] = _list_value(raw.get("people"))
    raw["actions"] = _list_value(raw.get("actions"))
    raw["text_visible"] = _list_value(raw.get("text_visible"))
    raw["confidence"] = raw.get("confidence") if raw.get("confidence") in {"high", "medium", "low"} else "medium"
    return raw


def _normalize_video_summary(raw: dict, frames: list[dict]) -> dict:
    title = _clean_text(raw.get("title"))
    short_summary = _clean_text(raw.get("short_summary"))
    detailed_summary = _clean_text(raw.get("detailed_summary"))
    scene = _clean_text(raw.get("scene"))
    timeline = _clean_text(raw.get("timeline"))

    fallback_caption = _clean_text(frames[0].get("caption")) if frames else ""
    if not title:
        title = _title_from_summary(short_summary or detailed_summary or fallback_caption)
    if not title:
        title = "未命名视频"

    raw["title"] = title
    raw["short_summary"] = short_summary or fallback_caption or title
    raw["detailed_summary"] = detailed_summary or short_summary or fallback_caption or title
    raw["scene"] = scene or _first_non_empty(frames, "scene") or "未知场景"
    raw["timeline"] = timeline or _timeline_from_frames(frames)
    raw["key_frames"] = _normalize_key_frames(raw.get("key_frames"), frames)
    raw["objects"] = _list_value(raw.get("objects")) or _merged_list(frames, "objects")
    raw["people"] = _list_value(raw.get("people")) or _merged_list(frames, "people")
    raw["actions"] = _list_value(raw.get("actions")) or _merged_list(frames, "actions")
    raw["text_visible"] = _list_value(raw.get("text_visible")) or _merged_list(frames, "text_visible")
    raw["search_keywords"] = _list_value(raw.get("search_keywords")) or [title, raw["scene"]]
    raw["location_guess"] = _clean_text(raw.get("location_guess")) or "unknown"
    raw["mood"] = _clean_text(raw.get("mood")) or "unknown"
    raw["confidence"] = raw.get("confidence") if raw.get("confidence") in {"high", "medium", "low"} else "medium"
    return raw


def _normalize_video_segment_analysis(
    raw: dict,
    previous_global_summary: str,
    previous_timeline: list[dict],
) -> dict:
    current_segment_summary = _clean_text(raw.get("current_segment_summary"))
    if not current_segment_summary:
        current_segment_summary = "当前片段未生成明确摘要"

    updated_global_summary = _clean_text(raw.get("updated_global_summary"))
    if not updated_global_summary:
        updated_global_summary = "\n".join(
            text for text in (previous_global_summary, current_segment_summary) if text
        )

    raw["current_segment_summary"] = current_segment_summary
    raw["current_segment_tags"] = _list_value(raw.get("current_segment_tags"))
    raw["important_objects"] = _list_value(raw.get("important_objects"))
    raw["ocr_text"] = _list_value(raw.get("ocr_text"))
    raw["new_objects_or_scenes"] = _list_value(raw.get("new_objects_or_scenes"))
    raw["updated_global_summary"] = updated_global_summary
    raw["updated_timeline"] = _timeline_value(raw.get("updated_timeline")) or previous_timeline
    raw["confidence"] = _float_between_zero_and_one(raw.get("confidence"))
    return raw


def _segment_payloads(segments: list[VideoSegmentSummary]) -> list[dict]:
    return [
        {
            "segment_index": segment.segment_index,
            "start_time_seconds": segment.start_time_seconds,
            "end_time_seconds": segment.end_time_seconds,
            "current_segment_summary": segment.current_segment_summary,
            "current_segment_tags": _list_value(segment.current_segment_tags),
            "important_objects": _list_value(segment.important_objects),
            "ocr_text": _list_value(segment.ocr_text),
            "new_objects_or_scenes": _list_value(segment.new_objects_or_scenes),
            "confidence": segment.confidence,
        }
        for segment in segments
    ]


def _normalize_video_final_summary(
    raw: dict,
    segments: list[VideoSegmentSummary],
    rolling_global_summary: str,
    rolling_timeline: list[dict],
) -> dict:
    segment_summaries = [
        _clean_text(segment.current_segment_summary)
        for segment in segments
        if _clean_text(segment.current_segment_summary)
    ]
    fallback_summary = _clean_text(rolling_global_summary) or "\n".join(segment_summaries)
    fallback_title = _title_from_summary(_clean_text(raw.get("short_summary")) or fallback_summary) or "Untitled video"
    segment_objects = _unique_list(
        value
        for segment in segments
        for value in _list_value(segment.important_objects)
    )
    segment_tags = _unique_list(
        value
        for segment in segments
        for value in _list_value(segment.current_segment_tags)
    )
    segment_ocr = _unique_list(
        value
        for segment in segments
        for value in _list_value(segment.ocr_text)
    )
    segment_scenes = _unique_list(
        value
        for segment in segments
        for value in _list_value(segment.new_objects_or_scenes)
    )
    segment_confidences = [
        float(segment.confidence)
        for segment in segments
        if segment.confidence is not None
    ]
    average_confidence = (
        sum(segment_confidences) / len(segment_confidences) if segment_confidences else None
    )

    raw["title"] = _clean_text(raw.get("title")) or fallback_title
    raw["short_summary"] = _clean_text(raw.get("short_summary")) or fallback_summary or raw["title"]
    raw["detailed_summary"] = (
        _clean_text(raw.get("detailed_summary"))
        or _join_segment_summaries(segments)
        or raw["short_summary"]
    )
    raw["timeline"] = _timeline_value(raw.get("timeline")) or rolling_timeline
    raw["scene"] = _clean_text(raw.get("scene")) or ", ".join(segment_scenes[:8]) or "video segments"
    raw["objects"] = _list_value(raw.get("objects")) or segment_objects
    raw["actions"] = _list_value(raw.get("actions")) or segment_tags
    raw["text_visible"] = _list_value(raw.get("text_visible")) or segment_ocr
    raw["search_keywords"] = _list_value(raw.get("search_keywords")) or _unique_list(
        [raw["title"], *segment_tags, *segment_objects, *segment_scenes, *segment_ocr]
    )
    raw["confidence"] = (
        raw.get("confidence")
        if raw.get("confidence") in {"high", "medium", "low"}
        else _confidence_label(average_confidence)
    )
    return raw


def _join_segment_summaries(segments: list[VideoSegmentSummary]) -> str:
    parts = []
    for segment in segments:
        summary = _clean_text(segment.current_segment_summary)
        if summary:
            parts.append(f"Segment {segment.segment_index}: {summary}")
    return "\n".join(parts)


def _frame_infos(frames: list[ExtractedFrame], *, start_index: int) -> list[dict]:
    return [
        {
            "image_order": offset + 1,
            "frame_index": start_index + offset,
            "timestamp_seconds": frame.timestamp_seconds,
            "timestamp": _format_timestamp(frame.timestamp_seconds),
        }
        for offset, frame in enumerate(frames)
    ]


def _format_timestamp(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _timeline_value(value: object) -> list[dict]:
    if isinstance(value, list):
        result = []
        for item in value:
            if not isinstance(item, dict):
                continue
            result.append(
                {
                    "start_time": _clean_text(item.get("start_time")) or "00:00:00",
                    "end_time": _clean_text(item.get("end_time")) or "00:00:00",
                    "summary": _clean_text(item.get("summary")) or "",
                }
            )
        return result
    return []


def _float_between_zero_and_one(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return min(max(number, 0.0), 1.0)


def _confidence_label(value: float | None) -> str:
    if value is None:
        return "medium"
    if value >= 0.75:
        return "high"
    if value >= 0.45:
        return "medium"
    return "low"


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


def _first_non_empty(items: list[dict], key: str) -> str:
    for item in items:
        value = _clean_text(item.get(key))
        if value:
            return value
    return ""


def _timeline_from_frames(frames: list[dict]) -> str:
    parts = []
    for frame in frames:
        timestamp = frame.get("timestamp_seconds")
        caption = _clean_text(frame.get("caption"))
        if caption:
            parts.append(f"{float(timestamp or 0):.1f}s: {caption}")
    return "\n".join(parts)


def _normalize_key_frames(value: object, fallback_frames: list[dict]) -> list[dict]:
    if isinstance(value, list):
        normalized = []
        for item in value:
            if not isinstance(item, dict):
                continue
            caption = _clean_text(item.get("caption"))
            if not caption:
                continue
            try:
                timestamp = float(item.get("timestamp_seconds") or 0)
            except (TypeError, ValueError):
                timestamp = 0.0
            normalized.append({"timestamp_seconds": timestamp, "caption": caption})
        if normalized:
            return normalized
    return [
        {
            "timestamp_seconds": float(frame.get("timestamp_seconds") or 0),
            "caption": _clean_text(frame.get("caption")),
        }
        for frame in fallback_frames
        if _clean_text(frame.get("caption"))
    ]


def _merged_list(items: list[dict], key: str) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in items:
        for value in _list_value(item.get(key)):
            if value in seen:
                continue
            seen.add(value)
            merged.append(value)
    return merged


def _unique_list(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _title_from_summary(text: str) -> str:
    text = text.strip().replace("\n", " ")
    if not text:
        return ""
    for separator in ("。", "，", ",", ".", "；", ";"):
        if separator in text:
            text = text.split(separator, 1)[0]
            break
    return text[:24]
