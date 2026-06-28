from __future__ import annotations

import uuid
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.path_utils import normalize_path, path_has_prefix
from app.models.db_models import DirectoryRule, MediaFile, SearchMessage
from app.models.schemas import ChatStreamRequest
from app.services.agent.types import (
    AgentContextPack,
    ConversationContext,
    LibraryContext,
    LibraryRootContext,
    RuntimeContext,
    UiContext,
)
from app.services.agent.utils import clean_text, clip_text, directory_name
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
    return AgentContextPack(
        runtime_context=runtime,
        library_context=library,
        ui_context=ui,
        conversation_context=conversation,
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


def _root_filter(path: str):
    from app.services.agent.utils import directory_filter

    return directory_filter(path)


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
