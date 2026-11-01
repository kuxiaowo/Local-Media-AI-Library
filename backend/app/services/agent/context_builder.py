from __future__ import annotations

import uuid
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.path_utils import normalize_path, path_has_prefix
from app.models.db_models import DirectoryRule, MediaAiSummary, MediaFile, SearchMessage
from app.models.schemas import ChatStreamRequest
from app.services.agent.types import (
    AgentContextPack,
    ConversationContext,
    LibraryContext,
    LibraryRootContext,
    RuntimeContext,
    UiContext,
    VisibleMemory,
)
from app.services.agent.utils import clean_text, clip_text, comparable_datetime, directory_name
from app.services.media_visibility import effective_enabled_rules, visible_media_filter


_FOLDER_SUMMARY_LIMIT = 120


def build_context_pack(
    db: Session,
    request: ChatStreamRequest,
    history: list[SearchMessage],
) -> AgentContextPack:
    runtime = _runtime_context(request)
    ui = _ui_context(request)
    library = _library_context(db, ui=ui, request=request)
    conversation = _conversation_context(history, ui=ui)
    visible_memory = _visible_memory_context(history, ui=ui, conversation=conversation)
    return AgentContextPack(
        runtime_context=runtime,
        library_context=library,
        ui_context=ui,
        conversation_context=conversation,
        user_question=request.message,
        visible_memory=visible_memory,
        library_overview=_library_overview(db),
        directory_tree=_directory_tree(db),
        directory_stats=_directory_stats(db),
    )


def _runtime_context(request: ChatStreamRequest) -> RuntimeContext:
    raw = request.context.runtime_context if request.context else None
    timezone_name = clean_text(getattr(raw, "timezone", None)) or "Asia/Shanghai"
    tz = _timezone_or_default(timezone_name)

    now = datetime.now(tz)
    now_iso = clean_text(getattr(raw, "now_iso", None)) or now.isoformat()
    today = clean_text(getattr(raw, "today", None)) or now.date().isoformat()
    locale = clean_text(getattr(raw, "locale", None)) or "zh-CN"
    try:
        recent_default_days = int(getattr(raw, "recent_default_days", 30) or 30)
    except (TypeError, ValueError):
        recent_default_days = 30
    return RuntimeContext(
        now_iso=now_iso,
        today=today,
        timezone=timezone_name,
        locale=locale,
        recent_default_days=max(1, min(365, recent_default_days)),
    )


def _timezone_or_default(timezone_name: str):
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        if timezone_name == "Asia/Shanghai":
            return timezone(timedelta(hours=8), name="Asia/Shanghai")
        return timezone.utc


def _ui_context(request: ChatStreamRequest) -> UiContext:
    raw = request.context.ui_context if request.context else None
    active_filters = dict(getattr(raw, "active_filters", None) or {})
    active_filters.setdefault("media_type", request.media_type)
    active_filters.setdefault("directory_path", request.directory_path)
    active_filters.setdefault("date_from", request.date_from.isoformat() if request.date_from else None)
    active_filters.setdefault("date_to", request.date_to.isoformat() if request.date_to else None)
    active_filters.setdefault("keyword", None)

    current_directory = clean_text(getattr(raw, "current_directory_path", None)) or clean_text(
        active_filters.get("directory_path")
    )
    return UiContext(
        page=clean_text(getattr(raw, "page", None)) or "agent",
        current_directory_path=normalize_path(current_directory) if current_directory else None,
        selected_media_ids=_string_uuid_list(getattr(raw, "selected_media_ids", None)),
        visible_media_ids=_string_uuid_list(getattr(raw, "visible_media_ids", None)),
        active_filters=active_filters,
    )


def _library_context(db: Session, *, ui: UiContext, request: ChatStreamRequest) -> LibraryContext:
    rules = effective_enabled_rules(list(db.scalars(select(DirectoryRule)).all()))
    roots = [_root_context(db, rule) for rule in rules]
    folder_tree_summary = _folder_tree_summary(db, roots=roots, ui=ui, request=request)
    return LibraryContext(roots=roots, folder_tree_summary=folder_tree_summary)


def _root_context(db: Session, rule: DirectoryRule) -> LibraryRootContext:
    rows = db.execute(
        select(
            MediaFile.media_type,
            MediaFile.status,
            func.count(MediaFile.id),
            func.min(MediaFile.captured_at),
            func.max(MediaFile.captured_at),
        )
        .where(visible_media_filter(db), _root_filter(rule.normalized_path))
        .group_by(MediaFile.media_type, MediaFile.status)
    ).all()
    media_count = image_count = video_count = done_count = pending_count = failed_count = 0
    date_min = None
    date_max = None
    for media_type, status, count, row_min, row_max in rows:
        count = int(count)
        media_count += count
        if media_type == "image":
            image_count += count
        elif media_type == "video":
            video_count += count
        if status == "done":
            done_count += count
        elif status == "failed":
            failed_count += count
        else:
            pending_count += count
        row_min = comparable_datetime(row_min)
        row_max = comparable_datetime(row_max)
        if row_min is not None and (date_min is None or row_min < date_min):
            date_min = row_min
        if row_max is not None and (date_max is None or row_max > date_max):
            date_max = row_max

    return LibraryRootContext(
        path=rule.normalized_path,
        display_name=directory_name(rule.path),
        enabled=bool(rule.enabled),
        recursive=bool(rule.recursive),
        media_count=media_count,
        image_count=image_count,
        video_count=video_count,
        done_count=done_count,
        pending_count=pending_count,
        failed_count=failed_count,
        date_min=date_min.isoformat() if date_min else None,
        date_max=date_max.isoformat() if date_max else None,
    )


def _folder_tree_summary(
    db: Session,
    *,
    roots: list[LibraryRootContext],
    ui: UiContext,
    request: ChatStreamRequest,
) -> list[dict[str, Any]]:
    rows = db.execute(
        select(
            MediaFile.parent_dir,
            MediaFile.media_type,
            MediaFile.status,
            func.count(MediaFile.id),
            func.min(MediaFile.captured_at),
            func.max(MediaFile.captured_at),
        )
        .where(visible_media_filter(db), MediaFile.parent_dir.is_not(None))
        .group_by(MediaFile.parent_dir, MediaFile.media_type, MediaFile.status)
    ).all()

    grouped: dict[str, dict[str, Any]] = {}
    for parent_dir, media_type, status, count, date_min, date_max in rows:
        if not parent_dir:
            continue
        item = grouped.setdefault(
            parent_dir,
            {
                "path": parent_dir,
                "name": directory_name(parent_dir),
                "media_count": 0,
                "image_count": 0,
                "video_count": 0,
                "done_count": 0,
                "pending_count": 0,
                "failed_count": 0,
                "date_min": None,
                "date_max": None,
                "reason": "top_directory",
            },
        )
        count = int(count)
        item["media_count"] += count
        if media_type == "image":
            item["image_count"] += count
        elif media_type == "video":
            item["video_count"] += count
        if status == "done":
            item["done_count"] += count
        elif status == "failed":
            item["failed_count"] += count
        else:
            item["pending_count"] += count
        if date_min is not None and (item["date_min"] is None or date_min.isoformat() < item["date_min"]):
            item["date_min"] = date_min.isoformat()
        if date_max is not None and (item["date_max"] is None or date_max.isoformat() > item["date_max"]):
            item["date_max"] = date_max.isoformat()

    selected: dict[str, dict[str, Any]] = {}
    for root in roots:
        selected[root.path] = {
            "path": root.path,
            "name": root.display_name,
            "media_count": root.media_count,
            "image_count": root.image_count,
            "video_count": root.video_count,
            "done_count": root.done_count,
            "pending_count": root.pending_count,
            "failed_count": root.failed_count,
            "date_min": root.date_min,
            "date_max": root.date_max,
            "reason": "enabled_root",
        }

    current = ui.current_directory_path
    if current:
        for path, item in grouped.items():
            if _near_current_directory(path, current):
                selected[path] = {**item, "reason": "near_current_directory"}

    query = " ".join(
        part
        for part in [
            request.message,
            clean_text(request.directory_path),
            clean_text(ui.active_filters.get("directory_path")),
        ]
        if part
    ).lower()
    if query:
        for path, item in grouped.items():
            haystack = f"{path} {item['name']}".lower()
            if any(token and token in haystack for token in _query_tokens(query)):
                selected[path] = {**item, "reason": "directory_hint_match"}

    for item in sorted(grouped.values(), key=lambda item: item["media_count"], reverse=True)[:40]:
        selected.setdefault(item["path"], item)

    return sorted(selected.values(), key=lambda item: item.get("media_count", 0), reverse=True)[:_FOLDER_SUMMARY_LIMIT]


def _conversation_context(history: list[SearchMessage], *, ui: UiContext) -> ConversationContext:
    last_assistant: SearchMessage | None = None
    for message in reversed(history):
        if message.role == "assistant":
            last_assistant = message
            break
    if last_assistant is None:
        return ConversationContext(last_selected_media_ids=ui.selected_media_ids)

    blocks = last_assistant.blocks if isinstance(last_assistant.blocks, list) else []
    shown: list[str] = []
    answer_parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "media_grid":
            for item in block.get("items") or []:
                if isinstance(item, dict):
                    media_id = clean_text(item.get("media_id"))
                    if media_id:
                        shown.append(media_id)
        elif block_type == "text":
            answer_parts.append(clean_text(block.get("text")))
        elif block_type == "summary":
            answer_parts.append(clean_text(block.get("text") or block.get("summary")))
        elif block_type == "question_answer":
            answer_parts.append(clean_text(block.get("answer")))

    last_intent = None
    last_scope = None
    tool_events = last_assistant.tool_events if isinstance(last_assistant.tool_events, list) else []
    for event in reversed(tool_events):
        if not isinstance(event, dict):
            continue
        if event.get("event") == "plan" and isinstance(event.get("plan"), dict):
            last_intent = clean_text(event["plan"].get("task_type")) or None
        if event.get("event") == "scope" and isinstance(event.get("scope"), dict):
            last_scope = event["scope"]
        if last_intent and last_scope:
            break

    return ConversationContext(
        last_intent=last_intent,
        last_scope=last_scope,
        last_shown_media_ids=_dedupe_string_ids(shown),
        last_selected_media_ids=ui.selected_media_ids,
        last_answer_summary=clip_text("\n".join(part for part in answer_parts if part) or last_assistant.content, 500),
    )


def _visible_memory_context(
    history: list[SearchMessage],
    *,
    ui: UiContext,
    conversation: ConversationContext,
) -> VisibleMemory:
    for message in reversed(history):
        if message.role != "assistant" or not isinstance(message.tool_events, list):
            continue
        for event in reversed(message.tool_events):
            if not isinstance(event, dict):
                continue
            payload = event.get("visible_memory")
            if event.get("event") == "visible_memory":
                payload = payload or event.get("memory")
            if isinstance(payload, dict):
                return VisibleMemory(
                    known_facts=_limited_strings(payload.get("known_facts"), limit=30),
                    checked_scopes=_limited_strings(payload.get("checked_scopes"), limit=30),
                    candidate_media_ids=_dedupe_string_ids(
                        _limited_strings(payload.get("candidate_media_ids"), limit=100)
                        + conversation.last_shown_media_ids
                        + ui.visible_media_ids
                    )[:100],
                    rejected_scopes=_limited_strings(payload.get("rejected_scopes"), limit=30),
                )
    return VisibleMemory(
        candidate_media_ids=_dedupe_string_ids(conversation.last_shown_media_ids + ui.visible_media_ids)[:100],
    )


def _library_overview(db: Session) -> dict[str, Any]:
    rows = db.execute(
        select(
            MediaFile.media_type,
            func.count(MediaFile.id),
            func.min(MediaFile.captured_at),
            func.max(MediaFile.captured_at),
        )
        .join(MediaAiSummary, MediaAiSummary.media_id == MediaFile.id)
        .where(MediaFile.status == "done", visible_media_filter(db))
        .group_by(MediaFile.media_type)
    ).all()
    media_count = image_count = video_count = 0
    earliest = None
    latest = None
    for media_type, count, row_min, row_max in rows:
        count = int(count)
        media_count += count
        if media_type == "image":
            image_count += count
        elif media_type == "video":
            video_count += count
        row_min = comparable_datetime(row_min)
        row_max = comparable_datetime(row_max)
        if row_min is not None and (earliest is None or row_min < earliest):
            earliest = row_min
        if row_max is not None and (latest is None or row_max > latest):
            latest = row_max

    directory_count = db.scalar(
        select(func.count(func.distinct(MediaFile.parent_dir)))
        .join(MediaAiSummary, MediaAiSummary.media_id == MediaFile.id)
        .where(MediaFile.status == "done", visible_media_filter(db), MediaFile.parent_dir.is_not(None))
    )
    return {
        "media_count": media_count,
        "image_count": image_count,
        "video_count": video_count,
        "directory_count": int(directory_count or 0),
        "earliest_captured_at": earliest.isoformat() if earliest else None,
        "latest_captured_at": latest.isoformat() if latest else None,
    }


def _directory_tree(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        select(MediaFile.parent_dir, func.count(MediaFile.id))
        .join(MediaAiSummary, MediaAiSummary.media_id == MediaFile.id)
        .where(MediaFile.status == "done", visible_media_filter(db), MediaFile.parent_dir.is_not(None))
        .group_by(MediaFile.parent_dir)
        .order_by(MediaFile.parent_dir)
    ).all()
    nodes: list[dict[str, Any]] = []
    for path, count in rows:
        if not path:
            continue
        normalized = normalize_path(path)
        parent_path = normalized.rsplit("/", 1)[0] if "/" in normalized else None
        nodes.append(
            {
                "path": normalized,
                "name": directory_name(normalized),
                "parent_path": parent_path,
                "depth": normalized.count("/"),
                "media_count": int(count),
            }
        )
    return nodes


def _directory_stats(db: Session) -> list[dict[str, Any]]:
    rules = effective_enabled_rules(list(db.scalars(select(DirectoryRule)).all()))
    rows = db.execute(
        select(MediaFile, MediaAiSummary)
        .join(MediaAiSummary, MediaAiSummary.media_id == MediaFile.id)
        .where(MediaFile.status == "done", visible_media_filter(db), MediaFile.parent_dir.is_not(None))
        .order_by(MediaFile.parent_dir)
    ).all()
    grouped: dict[str, dict[str, Any]] = {}
    keyword_counts: dict[str, Counter[str]] = {}
    for media, summary in rows:
        if not media.parent_dir:
            continue
        path = normalize_path(media.parent_dir)
        item = grouped.setdefault(
            path,
            {
                "path": path,
                "media_count": 0,
                "image_count": 0,
                "video_count": 0,
                "earliest_captured_at": None,
                "latest_captured_at": None,
                "background_context": _background_for_directory(path, rules),
                "common_keywords": [],
            },
        )
        item["media_count"] += 1
        if media.media_type == "image":
            item["image_count"] += 1
        elif media.media_type == "video":
            item["video_count"] += 1
        captured_at = comparable_datetime(media.captured_at)
        if captured_at is not None:
            captured_iso = captured_at.isoformat()
            if item["earliest_captured_at"] is None or captured_iso < item["earliest_captured_at"]:
                item["earliest_captured_at"] = captured_iso
            if item["latest_captured_at"] is None or captured_iso > item["latest_captured_at"]:
                item["latest_captured_at"] = captured_iso
        counter = keyword_counts.setdefault(path, Counter())
        counter.update(_keyword_values(summary.search_keywords))

    for path, counter in keyword_counts.items():
        grouped[path]["common_keywords"] = [keyword for keyword, _count in counter.most_common(12)]
    return sorted(grouped.values(), key=lambda item: item["path"])


def _root_filter(path: str):
    from app.services.agent.utils import directory_filter

    return directory_filter(path)


def _background_for_directory(path: str, rules: list[DirectoryRule]) -> str | None:
    normalized = normalize_path(path)
    matches = [
        rule
        for rule in rules
        if rule.background_context and path_has_prefix(normalized, rule.normalized_path)
    ]
    if not matches:
        return None
    matches.sort(key=lambda rule: len(rule.normalized_path or ""), reverse=True)
    return clip_text(matches[0].background_context, 500)


def _keyword_values(value: object) -> list[str]:
    result: list[str] = []
    if isinstance(value, list):
        raw_values = value
    elif isinstance(value, dict):
        raw_values = list(value.keys()) + list(value.values())
    else:
        raw_values = []
    for raw in raw_values:
        if isinstance(raw, (list, tuple)):
            result.extend(_keyword_values(list(raw)))
            continue
        text = clean_text(raw)
        if text:
            result.append(clip_text(text, 60))
    return result


def _near_current_directory(path: str, current: str) -> bool:
    path = normalize_path(path)
    current = normalize_path(current)
    if path_has_prefix(path, current) or path_has_prefix(current, path):
        return True
    current_parent = current.rsplit("/", 1)[0] if "/" in current else current
    return path_has_prefix(path, current_parent)


def _query_tokens(query: str) -> list[str]:
    raw = query.replace("\\", "/")
    tokens = [token for token in raw.split("/") if token]
    tokens.extend(replace for replace in raw.replace("_", " ").replace("-", " ").split() if replace)
    return [token.strip().lower() for token in tokens if len(token.strip()) >= 2]


def _string_uuid_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return _dedupe_string_ids(values)


def _dedupe_string_ids(values: list[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        try:
            media_id = str(uuid.UUID(str(value)))
        except (TypeError, ValueError):
            continue
        if media_id not in seen:
            result.append(media_id)
            seen.add(media_id)
    return result


def _limited_strings(values: object, *, limit: int) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clip_text(clean_text(value), 300)
        if text and text not in seen:
            result.append(text)
            seen.add(text)
        if len(result) >= limit:
            break
    return result
