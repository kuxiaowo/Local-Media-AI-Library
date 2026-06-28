from __future__ import annotations

import re
import uuid
from typing import Any

from app.core.path_utils import normalize_path
from app.models.schemas import ChatStreamRequest
from app.services.agent.types import AgentContextPack, AgentPlan, Scope
from app.services.agent.utils import clean_text, parse_datetime, parse_uuid_list


def resolve_scope(plan: AgentPlan, request: ChatStreamRequest, context_pack: AgentContextPack) -> Scope:
    ui = context_pack.ui_context
    conversation = context_pack.conversation_context
    active_filters = ui.active_filters or {}
    source = plan.scope_reference
    directory_paths: list[str] = []
    media_ids: list[uuid.UUID] = []
    explain_parts: list[str] = []

    if source == "current_directory":
        current = clean_text(ui.current_directory_path)
        if current:
            directory_paths = [normalize_path(current)]
            explain_parts.append("用户指向当前文件夹，使用 ui_context.current_directory_path。")
        else:
            source = "global"
            explain_parts.append("当前文件夹缺失，回退到全库启用根目录。")
    elif source == "selected_media":
        media_ids = parse_uuid_list(ui.selected_media_ids)
        explain_parts.append("用户指向选中媒体，使用 ui_context.selected_media_ids。")
    elif source == "visible_media":
        media_ids = parse_uuid_list(ui.visible_media_ids)
        explain_parts.append("用户指向当前可见媒体，使用 ui_context.visible_media_ids。")
    elif source == "previous_results":
        media_ids = parse_uuid_list(conversation.last_shown_media_ids)
        explain_parts.append("用户指向上一轮结果，使用 conversation_context.last_shown_media_ids。")
    elif source == "explicit_directory":
        matched = _match_directory_hint(plan.directory_hint or request.message, context_pack)
        if matched:
            directory_paths = matched
            explain_parts.append("用户提到目录，已在压缩目录摘要中做模糊匹配。")
        elif ui.current_directory_path:
            directory_paths = [ui.current_directory_path]
            explain_parts.append("目录名未匹配，但 UI 有当前目录，继承当前目录。")
        else:
            source = "global"
            explain_parts.append("目录名未匹配，回退全库。")
    elif source == "global":
        directory_paths = [root.path for root in context_pack.library_context.roots if root.enabled]
        explain_parts.append("范围为全库，使用所有 enabled library roots。")

    explicit_global = _explicit_global(request.message)
    inherited_directory = clean_text(request.directory_path) or clean_text(active_filters.get("directory_path"))
    if not directory_paths and not media_ids and not explicit_global and inherited_directory:
        directory_paths = [normalize_path(inherited_directory)]
        explain_parts.append("继承请求或 UI 的目录过滤。")
    elif directory_paths and inherited_directory and not explicit_global and source in {"global", "explicit_time_range"}:
        directory_paths = [normalize_path(inherited_directory)]
        explain_parts.append("除非用户要求全库，否则继承当前 UI 目录过滤。")

    if not directory_paths and source == "global":
        directory_paths = [root.path for root in context_pack.library_context.roots if root.enabled]

    media_type = plan.media_type
    request_media_type = clean_text(request.media_type)
    filter_media_type = clean_text(active_filters.get("media_type"))
    if media_type == "any":
        if request_media_type in {"image", "video"}:
            media_type = request_media_type
            explain_parts.append("继承请求媒体类型过滤。")
        elif filter_media_type in {"image", "video"}:
            media_type = filter_media_type
            explain_parts.append("继承 UI 媒体类型过滤。")

    date_from = plan.date_from or request.date_from or parse_datetime(active_filters.get("date_from"))
    date_to = plan.date_to or request.date_to or parse_datetime(active_filters.get("date_to"), end_of_day=True)
    if plan.date_from or plan.date_to:
        explain_parts.append("使用 Planner 从用户表达解析出的时间范围。")
    elif request.date_from or request.date_to or active_filters.get("date_from") or active_filters.get("date_to"):
        explain_parts.append("继承请求或 UI 时间过滤。")

    return Scope(
        source=source,
        media_type=media_type if media_type in {"image", "video", "any"} else "any",
        directory_paths=_dedupe_paths(directory_paths),
        media_ids=media_ids,
        date_from=date_from,
        date_to=date_to,
        analysis_status="done",
        limit_hint=max(1, min(1000, request.limit)),
        explain=" ".join(explain_parts),
    )


def _match_directory_hint(hint: str | None, context_pack: AgentContextPack) -> list[str]:
    text = clean_text(hint)
    if not text:
        return []
    normalized_hint = normalize_path(text)
    query = text.lower()
    matches: list[tuple[int, str]] = []
    for item in context_pack.library_context.folder_tree_summary:
        path = clean_text(item.get("path"))
        name = clean_text(item.get("name"))
        haystack = f"{path} {name}".lower()
        score = 0
        if normalized_hint and normalized_hint == path:
            score += 100
        if normalized_hint and normalized_hint in path:
            score += 60
        if query and query in haystack:
            score += 50
        for token in _tokens(query):
            if token in haystack:
                score += 10
        if score > 0 and path:
            matches.append((score, path))
    matches.sort(key=lambda item: item[0], reverse=True)
    return _dedupe_paths([path for _score, path in matches[:10]])


def _explicit_global(message: str) -> bool:
    return re.search(r"全库|全部媒体|所有媒体|不要限制|不限制当前目录", message) is not None


def _tokens(text: str) -> list[str]:
    return [token for token in re.split(r"[\s/\\,_-]+", text) if len(token) >= 2]


def _dedupe_paths(paths: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for path in paths:
        normalized = normalize_path(path)
        if normalized and normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return result

