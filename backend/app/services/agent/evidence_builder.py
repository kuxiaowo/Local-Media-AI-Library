from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.db_models import MediaAiSummary, MediaFile, VideoFrameSummary, VideoSegmentSummary
from app.services.agent.types import MediaCandidate
from app.services.agent.utils import jsonish
from app.services.media_visibility import visible_media_filter


DetailLevel = str


def build_evidence(
    db: Session,
    media_ids: list[uuid.UUID],
    *,
    candidates: list[MediaCandidate] | None = None,
    detail_level: DetailLevel = "detailed",
) -> list[dict[str, Any]]:
    if not media_ids:
        return []
    candidate_map = {candidate.media_id: candidate for candidate in candidates or []}
    ordered_ids = _dedupe_ids(media_ids)
    rows = db.execute(
        select(MediaFile, MediaAiSummary)
        .join(MediaAiSummary, MediaAiSummary.media_id == MediaFile.id)
        .where(MediaFile.id.in_(ordered_ids), MediaFile.status == "done", visible_media_filter(db))
    ).all()
    by_id = {media.id: (media, summary) for media, summary in rows}
    segments = _segments_by_media(db, ordered_ids) if detail_level in {"video_timeline", "full"} else {}
    frames = _frames_by_media(db, ordered_ids) if detail_level in {"video_timeline", "full"} else {}

    evidence: list[dict[str, Any]] = []
    for media_id in ordered_ids:
        pair = by_id.get(media_id)
        if pair is None:
            continue
        media, summary = pair
        candidate = candidate_map.get(media_id)
        item = _base_evidence(media, summary, candidate=candidate, detail_level=detail_level)
        if media.media_type == "video" and detail_level in {"video_timeline", "full"}:
            item["duration_seconds"] = media.duration_seconds
            item["video_segments"] = [_segment_payload(segment) for segment in segments.get(media.id, [])]
            item["video_frames"] = [_frame_payload(frame) for frame in frames.get(media.id, [])[:30]]
        evidence.append(item)
    return evidence


def _base_evidence(
    media: MediaFile,
    summary: MediaAiSummary,
    *,
    candidate: MediaCandidate | None,
    detail_level: DetailLevel,
) -> dict[str, Any]:
    path_hint = media.parent_dir or media.root_path or media.path
    payload: dict[str, Any] = {
        "media_id": str(media.id),
        "media_type": media.media_type,
        "filename": media.path.replace("\\", "/").rsplit("/", 1)[-1],
        "path_hint": path_hint,
        "path": media.path,
        "parent_dir": media.parent_dir,
        "root_path": media.root_path,
        "captured_at": media.captured_at.isoformat() if media.captured_at else None,
        "width": media.width,
        "height": media.height,
        "duration_seconds": media.duration_seconds,
        "title": summary.title,
        "short_summary": summary.short_summary,
        "searchable_text": summary.searchable_text if detail_level != "light" else _clip(summary.searchable_text, 600),
        "initial_score": candidate.score if candidate else 0.0,
        "initial_reason": candidate.reason if candidate else "",
    }
    if detail_level in {"detailed", "video_timeline", "full"}:
        payload.update(
            {
                "detailed_summary": summary.detailed_summary,
                "scene": summary.scene,
                "objects": jsonish(summary.objects),
                "people": jsonish(summary.people),
                "actions": jsonish(summary.actions),
                "text_visible": jsonish(summary.text_visible),
                "location_guess": summary.location_guess,
                "time_clues": summary.time_clues,
                "mood": summary.mood,
                "search_keywords": jsonish(summary.search_keywords),
                "raw_json": jsonish(summary.raw_json) if detail_level == "full" else None,
                "confidence": summary.confidence,
            }
        )
    return payload


def _segments_by_media(db: Session, media_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[VideoSegmentSummary]]:
    rows = db.scalars(
        select(VideoSegmentSummary)
        .where(VideoSegmentSummary.media_id.in_(media_ids))
        .order_by(VideoSegmentSummary.media_id, VideoSegmentSummary.segment_index)
    ).all()
    grouped: dict[uuid.UUID, list[VideoSegmentSummary]] = {}
    for segment in rows:
        grouped.setdefault(segment.media_id, []).append(segment)
    return grouped


def _frames_by_media(db: Session, media_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[VideoFrameSummary]]:
    rows = db.scalars(
        select(VideoFrameSummary)
        .where(VideoFrameSummary.media_id.in_(media_ids))
        .order_by(VideoFrameSummary.media_id, VideoFrameSummary.timestamp_seconds)
    ).all()
    grouped: dict[uuid.UUID, list[VideoFrameSummary]] = {}
    for frame in rows:
        grouped.setdefault(frame.media_id, []).append(frame)
    return grouped


def _segment_payload(segment: VideoSegmentSummary) -> dict[str, Any]:
    return {
        "segment_index": segment.segment_index,
        "start_time_seconds": segment.start_time_seconds,
        "end_time_seconds": segment.end_time_seconds,
        "current_segment_summary": segment.current_segment_summary,
        "important_observations": jsonish(segment.important_observations),
        "current_segment_tags": jsonish(segment.current_segment_tags),
        "important_objects": jsonish(segment.important_objects),
        "new_objects_or_scenes": jsonish(segment.new_objects_or_scenes),
        "updated_global_summary": segment.updated_global_summary,
        "uncertain_points": jsonish(segment.uncertain_points),
        "confidence": segment.confidence,
    }


def _frame_payload(frame: VideoFrameSummary) -> dict[str, Any]:
    return {
        "frame_index": frame.frame_index,
        "timestamp_seconds": frame.timestamp_seconds,
        "caption": frame.caption,
        "objects": jsonish(frame.objects),
        "people": jsonish(frame.people),
        "actions": jsonish(frame.actions),
        "text_visible": jsonish(frame.text_visible),
    }


def _dedupe_ids(media_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    result: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for media_id in media_ids:
        if media_id not in seen:
            result.append(media_id)
            seen.add(media_id)
    return result


def _clip(text: str | None, max_chars: int) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."

