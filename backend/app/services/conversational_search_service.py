from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.path_utils import normalize_path
from app.models.db_models import DirectoryRule, EmbeddingProfile, MediaAiSummary, MediaEmbedding, MediaFile, SearchMessage
from app.models.schemas import ChatStreamRequest
from app.services.media_visibility import effective_enabled_rules, visible_media_filter
from app.services.ollama_client import OllamaClient
from app.services.search_rerank import keyword_score
from app.services.vector_math import cosine_similarity


AGENT_TOOLS: tuple[dict[str, str], ...] = (
    {
        "name": "list_directories",
        "description": "查看媒体库目录列表，适合用户问有哪些目录或不清楚目标目录。",
    },
    {
        "name": "search_directory",
        "description": "在指定目录或目录关键词下读取候选媒体。",
    },
    {
        "name": "global_vector_search",
        "description": "用语义向量做全局召回，适合常规找照片或视频。",
    },
    {
        "name": "scan_all_summaries",
        "description": "分批阅读当前范围内所有已分析媒体摘要，适合复杂条件、推荐、去重、总结和需要人工判断的任务。",
    },
    {
        "name": "get_media_details",
        "description": "读取对话中已经出现过的具体媒体详情。",
    },
)
AGENT_TOOL_NAMES = tuple(tool["name"] for tool in AGENT_TOOLS)

AGENT_SYSTEM_PROMPT = (
    "你是本地媒体库的对话式检索助手。你可以决定是否调用受控工具来查看目录、召回媒体或读取摘要。"
    "工具只负责找材料，最终筛选和排序不是工具，而是你在拿到候选后的强制最终判断阶段。"
    "如果用户的问题不需要媒体候选，可以直接回答。所有用户可见文本使用简体中文。"
)

ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "enum": ["answer_direct", "use_tools"]},
        "direct_answer": {"type": ["string", "null"]},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tool": {
                        "type": "string",
                        "enum": list(AGENT_TOOL_NAMES),
                    },
                    "reason": {"type": "string"},
                    "query": {"type": ["string", "null"]},
                    "directory_query": {"type": ["string", "null"]},
                    "directory_path": {"type": ["string", "null"]},
                    "media_ids": {"type": "array", "items": {"type": "string"}},
                    "limit": {"type": ["integer", "null"]},
                },
                "required": ["tool", "reason"],
            },
        },
        "final_instruction": {"type": ["string", "null"]},
    },
    "required": ["mode", "direct_answer", "actions", "final_instruction"],
}

SCAN_SELECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "selections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "media_id": {"type": "string"},
                    "score": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["media_id", "score", "reason"],
            },
        }
    },
    "required": ["selections"],
}

FINAL_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["text", "media_grid"]},
                    "text": {"type": ["string", "null"]},
                    "title": {"type": ["string", "null"]},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "media_id": {"type": "string"},
                                "score": {"type": "number"},
                                "reason": {"type": "string"},
                            },
                            "required": ["media_id", "score", "reason"],
                        },
                    },
                },
                "required": ["type"],
            },
        },
    },
    "required": ["answer", "blocks"],
}

_MAX_ACTIONS = len(AGENT_TOOLS)
_MAX_DISPLAY_MEDIA = 30
_FINAL_CANDIDATE_TEXT_LIMIT = 1200
_SCAN_CHUNK_MAX_CHARS = 22000
_DIRECTORY_HINT_LIMIT = 120


@dataclass(frozen=True)
class AgentEvent:
    event: str
    data: dict[str, Any]


@dataclass(frozen=True)
class MediaCandidate:
    media_id: uuid.UUID
    path: str
    media_type: str
    captured_at: datetime | None
    parent_dir: str | None
    title: str | None
    short_summary: str | None
    searchable_text: str
    score: float = 0.0
    reason: str = ""

    def to_item(self, *, reason: str | None = None, score: float | None = None) -> dict[str, Any]:
        return {
            "media_id": str(self.media_id),
            "path": self.path,
            "thumbnail_url": f"/api/media/{self.media_id}/thumbnail",
            "media_type": self.media_type,
            "captured_at": self.captured_at.isoformat() if self.captured_at else None,
            "title": self.title,
            "short_summary": self.short_summary,
            "match_reason": reason or self.reason or "AI 判断与请求相关",
            "score": round(_clamp(score if score is not None else self.score), 6),
        }


async def run_agent_turn_events(
    db: Session,
    request: ChatStreamRequest,
    history: list[SearchMessage],
    ollama: OllamaClient,
) -> AsyncIterator[AgentEvent]:
    settings = get_settings()
    model_name = settings.default_ai_search_model.strip() or settings.default_summary_model
    directory_hints = _directory_hints(db)
    decision = await _decide_actions(
        ollama=ollama,
        model_name=model_name,
        request=request,
        history=history,
        directory_hints=directory_hints,
    )

    actions = _normalize_actions(decision)
    candidates: list[MediaCandidate] = []
    tool_notes: list[str] = []

    if not actions and _clean_text(decision.get("mode")) == "answer_direct":
        answer = _clean_text(decision.get("direct_answer")) or "我理解了。"
        blocks = [{"type": "text", "text": answer}]
        async for event in _stream_blocks(blocks):
            yield event
        yield AgentEvent("assistant_message", {"content": answer, "blocks": blocks, "ai_model": model_name})
        return

    if not actions:
        actions = [
            {
                "tool": "global_vector_search",
                "reason": "默认使用全局向量检索召回可能相关的媒体。",
                "query": request.message,
                "limit": request.candidate_k,
            }
        ]

    for action in actions[:_MAX_ACTIONS]:
        tool = _clean_text(action.get("tool"))
        reason = _clean_text(action.get("reason")) or "根据当前问题选择工具。"
        yield AgentEvent("tool_call", {"tool": tool, "reason": reason})

        try:
            if tool == "list_directories":
                directories = _list_directories(db)
                tool_notes.append(_directories_note(directories))
                yield AgentEvent("tool_result", {"tool": tool, "count": len(directories), "summary": f"读取到 {len(directories)} 个目录。"})
            elif tool == "search_directory":
                found = _search_directory(db, request, action, directory_hints)
                candidates = _merge_candidates(candidates, found)
                yield AgentEvent("tool_result", {"tool": tool, "count": len(found), "summary": f"目录候选 {len(found)} 项。"})
            elif tool == "global_vector_search":
                found = await _global_vector_search(db, ollama, request, action)
                candidates = _merge_candidates(candidates, found)
                yield AgentEvent("tool_result", {"tool": tool, "count": len(found), "summary": f"全局向量召回 {len(found)} 项。"})
            elif tool == "scan_all_summaries":
                found = await _scan_all_summaries(db, ollama, model_name, request, action)
                candidates = _merge_candidates(candidates, found)
                yield AgentEvent("tool_result", {"tool": tool, "count": len(found), "summary": f"分批阅读摘要后保留 {len(found)} 项。"})
            elif tool == "get_media_details":
                found = _get_media_details(db, action)
                candidates = _merge_candidates(candidates, found)
                yield AgentEvent("tool_result", {"tool": tool, "count": len(found), "summary": f"读取媒体详情 {len(found)} 项。"})
            else:
                yield AgentEvent("tool_result", {"tool": tool or "unknown", "count": 0, "summary": "忽略未知工具。"})
        except Exception as exc:
            yield AgentEvent("tool_result", {"tool": tool or "unknown", "count": 0, "summary": f"工具失败：{exc}"})

    final = await _ask_final_response(
        ollama=ollama,
        model_name=model_name,
        request=request,
        history=history,
        candidates=candidates,
        tool_notes=tool_notes,
        final_instruction=_clean_text(decision.get("final_instruction")),
    )
    blocks = _validated_blocks(final, candidates, display_limit=min(request.limit, _MAX_DISPLAY_MEDIA))
    content = _blocks_to_text(blocks) or _clean_text(final.get("answer")) or "已完成。"
    async for event in _stream_blocks(blocks):
        yield event
    yield AgentEvent(
        "assistant_message",
        {
            "content": content,
            "blocks": blocks,
            "ai_model": model_name,
            "candidate_count": len(candidates),
        },
    )


async def _decide_actions(
    *,
    ollama: OllamaClient,
    model_name: str,
    request: ChatStreamRequest,
    history: list[SearchMessage],
    directory_hints: list[dict[str, Any]],
) -> dict[str, Any]:
    hints_text = "\n".join(
        f"- {item['name']}: {item['path']} ({item.get('count', 0)} 项)"
        for item in directory_hints[:_DIRECTORY_HINT_LIMIT]
    )
    prompt = f"""今天日期：{datetime.now().date().isoformat()}

对话历史：
{_history_text(history)}

当前用户输入：
{request.message}

用户界面过滤条件：
{json.dumps(_request_filter_payload(request), ensure_ascii=False)}

可见目录提示：
{hints_text or "无"}

请决定是否需要调用工具。可用工具：
{_tool_catalog_text()}

如果问题只是解释系统、闲聊、澄清能力，不需要媒体候选，mode 用 answer_direct。
如果需要读取目录或媒体作为证据，mode 用 use_tools，并从可用工具里自行选择需要的工具。最终筛选排序不要作为工具输出。
"""
    try:
        return await ollama.generate_text_json(
            model=model_name,
            prompt=prompt,
            schema=ACTION_SCHEMA,
            system_prompt=AGENT_SYSTEM_PROMPT,
        )
    except Exception:
        return {
            "mode": "use_tools",
            "direct_answer": None,
            "actions": [
                {
                    "tool": "global_vector_search",
                    "reason": "规划失败，使用默认全局向量检索。",
                    "query": request.message,
                    "limit": request.candidate_k,
                }
            ],
            "final_instruction": None,
        }


def _normalize_actions(decision: dict[str, Any]) -> list[dict[str, Any]]:
    if _clean_text(decision.get("mode")) != "use_tools":
        return []
    actions = decision.get("actions")
    if not isinstance(actions, list):
        return []
    normalized: list[dict[str, Any]] = []
    for action in actions:
        if isinstance(action, dict) and _clean_text(action.get("tool")):
            normalized.append(action)
    return normalized


def _list_directories(db: Session) -> list[dict[str, Any]]:
    return _directory_hints(db)


def _search_directory(
    db: Session,
    request: ChatStreamRequest,
    action: dict[str, Any],
    directory_hints: list[dict[str, Any]],
) -> list[MediaCandidate]:
    directory_path = _clean_text(action.get("directory_path")) or request.directory_path
    if not directory_path:
        directory_query = _clean_text(action.get("directory_query")) or _clean_text(action.get("query"))
        matches = _resolve_directory_paths(directory_query, directory_hints)
        directory_path = matches[0] if matches else ""
    if not directory_path:
        return []

    query = _clean_text(action.get("query")) or request.message
    stmt = _scoped_media_summary_stmt(db, request).where(_directory_filter(directory_path))
    stmt = stmt.order_by(MediaFile.captured_at.is_(None), MediaFile.captured_at.desc(), MediaFile.created_at.desc())
    limit = _action_limit(action, request.candidate_k)
    rows = db.execute(stmt.limit(limit)).all()
    candidates = [_candidate_from_row(media, summary) for media, summary in rows]
    return _score_by_keywords(candidates, query, reason_prefix=f"来自目录 {directory_path}")


async def _global_vector_search(
    db: Session,
    ollama: OllamaClient,
    request: ChatStreamRequest,
    action: dict[str, Any],
) -> list[MediaCandidate]:
    model_name = get_settings().default_embedding_model.strip()
    if not model_name:
        return []
    profile = db.scalar(select(EmbeddingProfile).where(EmbeddingProfile.model_name == model_name))
    if profile is None:
        return []

    query = _clean_text(action.get("query")) or request.message
    query_vector = await ollama.embed_text(model=model_name, text=query)
    stmt = (
        select(MediaFile, MediaAiSummary, MediaEmbedding.embedding)
        .join(MediaAiSummary, MediaAiSummary.media_id == MediaFile.id)
        .join(MediaEmbedding, MediaEmbedding.media_id == MediaFile.id)
        .where(MediaEmbedding.profile_id == profile.id, MediaFile.status == "done", visible_media_filter(db))
    )
    stmt = _apply_request_filters(stmt, request)
    rows = db.execute(stmt).all()
    candidates: list[MediaCandidate] = []
    for media, summary, embedding in rows:
        vector_score = max(0.0, min(1.0, cosine_similarity(query_vector, embedding)))
        text_score = keyword_score(query, summary.searchable_text or "")
        score = round(0.8 * vector_score + 0.2 * text_score, 6)
        candidates.append(
            _candidate_from_row(
                media,
                summary,
                score=score,
                reason=f"向量相似度 {vector_score:.3f}，关键词匹配 {text_score:.3f}",
            )
        )
    candidates.sort(key=lambda item: item.score, reverse=True)
    return candidates[: _action_limit(action, request.candidate_k)]


async def _scan_all_summaries(
    db: Session,
    ollama: OllamaClient,
    model_name: str,
    request: ChatStreamRequest,
    action: dict[str, Any],
) -> list[MediaCandidate]:
    query = _clean_text(action.get("query")) or request.message
    rows = db.execute(
        _scoped_media_summary_stmt(db, request).order_by(
            MediaFile.captured_at.is_(None),
            MediaFile.captured_at.desc(),
            MediaFile.created_at.desc(),
        )
    ).all()
    all_candidates = [_candidate_from_row(media, summary) for media, summary in rows]
    if not all_candidates:
        return []

    selected: list[MediaCandidate] = []
    for chunk in _pack_candidate_chunks(all_candidates, _SCAN_CHUNK_MAX_CHARS, text_limit=700):
        prompt = f"""用户正在让你人工检索媒体摘要。

用户请求：
{query}

请只从本批候选里挑出相关媒体，最多 25 项。media_id 必须来自候选列表。

候选：
{_candidates_text(chunk, text_limit=700)}
"""
        try:
            raw = await ollama.generate_text_json(
                model=model_name,
                prompt=prompt,
                schema=SCAN_SELECTION_SCHEMA,
                system_prompt=AGENT_SYSTEM_PROMPT,
            )
            selected = _merge_candidates(selected, _selected_candidates(raw, chunk))
        except Exception:
            fallback = _score_by_keywords(chunk, query, reason_prefix="摘要关键词匹配")
            selected = _merge_candidates(selected, fallback[:25])

    selected.sort(key=lambda item: item.score, reverse=True)
    return selected[: _action_limit(action, request.candidate_k)]


def _get_media_details(db: Session, action: dict[str, Any]) -> list[MediaCandidate]:
    raw_ids = action.get("media_ids")
    if not isinstance(raw_ids, list):
        return []
    media_ids: list[uuid.UUID] = []
    for raw_id in raw_ids:
        try:
            media_ids.append(uuid.UUID(str(raw_id)))
        except ValueError:
            continue
    if not media_ids:
        return []
    rows = db.execute(
        select(MediaFile, MediaAiSummary)
        .join(MediaAiSummary, MediaAiSummary.media_id == MediaFile.id)
        .where(MediaFile.id.in_(media_ids), MediaFile.status == "done", visible_media_filter(db))
    ).all()
    by_id = {media.id: _candidate_from_row(media, summary, reason="读取了媒体详情") for media, summary in rows}
    return [by_id[media_id] for media_id in media_ids if media_id in by_id]


async def _ask_final_response(
    *,
    ollama: OllamaClient,
    model_name: str,
    request: ChatStreamRequest,
    history: list[SearchMessage],
    candidates: list[MediaCandidate],
    tool_notes: list[str],
    final_instruction: str,
) -> dict[str, Any]:
    prompt = f"""对话历史：
{_history_text(history)}

当前用户请求：
{request.message}

界面过滤条件：
{json.dumps(_request_filter_payload(request), ensure_ascii=False)}

工具补充信息：
{chr(10).join(tool_notes) if tool_notes else "无"}

候选媒体数量：{len(candidates)}

候选媒体：
{_candidates_text(candidates, text_limit=_FINAL_CANDIDATE_TEXT_LIMIT) if candidates else "无"}

额外最终指令：
{final_instruction or "无"}

请输出最终回答 blocks：
- 可以穿插多个 text 和 media_grid block，顺序由你决定。
- 如果有候选媒体，必须由你最终筛选，media_grid 的顺序就是最终排序。
- 如果用户只需要总结、判断或解释，可以使用候选作为证据，但最终只输出 text block，不必展示媒体卡片。
- 所有 media_grid 合计最多返回 {min(request.limit, _MAX_DISPLAY_MEDIA)} 个媒体。
- media_id 必须来自候选列表，不要编造。
- 如果任务只需要一两张代表图，就只放一两项。
- 如果没有候选或不适合展示媒体，只输出 text block。
"""
    try:
        return await ollama.generate_text_json(
            model=model_name,
            prompt=prompt,
            schema=FINAL_RESPONSE_SCHEMA,
            system_prompt=AGENT_SYSTEM_PROMPT,
        )
    except Exception:
        if not candidates:
            return {"answer": "没有找到可用于回答的媒体候选。", "blocks": [{"type": "text", "text": "没有找到可用于回答的媒体候选。"}]}
        fallback_items = [
            {"media_id": str(candidate.media_id), "score": candidate.score, "reason": candidate.reason or "候选相关"}
            for candidate in candidates[: min(request.limit, _MAX_DISPLAY_MEDIA)]
        ]
        return {
            "answer": "我先按召回结果整理了这些候选。",
            "blocks": [
                {"type": "text", "text": "我先按召回结果整理了这些候选。"},
                {"type": "media_grid", "title": "候选媒体", "items": fallback_items},
            ],
        }


def _validated_blocks(
    raw: dict[str, Any],
    candidates: list[MediaCandidate],
    display_limit: int = _MAX_DISPLAY_MEDIA,
) -> list[dict[str, Any]]:
    by_id = {str(candidate.media_id): candidate for candidate in candidates}
    blocks: list[dict[str, Any]] = []
    seen_media: set[str] = set()
    remaining_media = max(0, min(display_limit, _MAX_DISPLAY_MEDIA))

    for raw_block in raw.get("blocks") or []:
        if not isinstance(raw_block, dict):
            continue
        block_type = _clean_text(raw_block.get("type"))
        if block_type == "text":
            text = _clean_text(raw_block.get("text"))
            if text:
                blocks.append({"type": "text", "text": text})
        elif block_type == "media_grid":
            items: list[dict[str, Any]] = []
            for raw_item in raw_block.get("items") or []:
                if remaining_media <= 0:
                    break
                if not isinstance(raw_item, dict):
                    continue
                media_id = _clean_text(raw_item.get("media_id"))
                if not media_id or media_id in seen_media or media_id not in by_id:
                    continue
                seen_media.add(media_id)
                remaining_media -= 1
                items.append(
                    by_id[media_id].to_item(
                        reason=_clean_text(raw_item.get("reason")) or None,
                        score=_score_value(raw_item.get("score"), by_id[media_id].score),
                    )
                )
            if items:
                title = _clean_text(raw_block.get("title"))
                blocks.append({"type": "media_grid", "title": title or None, "items": items})

    if not blocks:
        answer = _clean_text(raw.get("answer")) or "已完成。"
        blocks.append({"type": "text", "text": answer})
    return blocks


async def _stream_blocks(blocks: list[dict[str, Any]]) -> AsyncIterator[AgentEvent]:
    for index, block in enumerate(blocks):
        block_id = f"block-{index}"
        if block.get("type") == "text":
            yield AgentEvent("text_start", {"block_id": block_id})
            for char in str(block.get("text") or ""):
                yield AgentEvent("text_delta", {"block_id": block_id, "text": char})
                await asyncio.sleep(0)
            yield AgentEvent("text_end", {"block_id": block_id})
        elif block.get("type") == "media_grid":
            yield AgentEvent("media_block", {"block_id": block_id, **block})


def _scoped_media_summary_stmt(db: Session, request: ChatStreamRequest):
    stmt = (
        select(MediaFile, MediaAiSummary)
        .join(MediaAiSummary, MediaAiSummary.media_id == MediaFile.id)
        .where(MediaFile.status == "done", visible_media_filter(db))
    )
    return _apply_request_filters(stmt, request)


def _apply_request_filters(stmt, request: ChatStreamRequest):
    if request.media_type != "any":
        stmt = stmt.where(MediaFile.media_type == request.media_type)
    if request.date_from is not None:
        stmt = stmt.where(MediaFile.captured_at >= request.date_from)
    if request.date_to is not None:
        stmt = stmt.where(MediaFile.captured_at <= request.date_to)
    if request.directory_path:
        stmt = stmt.where(_directory_filter(request.directory_path))
    return stmt


def _candidate_from_row(
    media: MediaFile,
    summary: MediaAiSummary,
    *,
    score: float = 0.0,
    reason: str = "",
) -> MediaCandidate:
    return MediaCandidate(
        media_id=media.id,
        path=media.path,
        media_type=media.media_type,
        captured_at=media.captured_at,
        parent_dir=media.parent_dir,
        title=summary.title,
        short_summary=summary.short_summary,
        searchable_text=summary.searchable_text or "",
        score=score,
        reason=reason,
    )


def _score_by_keywords(candidates: list[MediaCandidate], query: str, *, reason_prefix: str) -> list[MediaCandidate]:
    scored = [
        MediaCandidate(
            media_id=candidate.media_id,
            path=candidate.path,
            media_type=candidate.media_type,
            captured_at=candidate.captured_at,
            parent_dir=candidate.parent_dir,
            title=candidate.title,
            short_summary=candidate.short_summary,
            searchable_text=candidate.searchable_text,
            score=keyword_score(query, candidate.searchable_text),
            reason=reason_prefix,
        )
        for candidate in candidates
    ]
    scored.sort(key=lambda item: item.score, reverse=True)
    return scored


def _selected_candidates(raw: dict[str, Any], candidates: list[MediaCandidate]) -> list[MediaCandidate]:
    by_id = {str(candidate.media_id): candidate for candidate in candidates}
    selected: list[MediaCandidate] = []
    for raw_item in raw.get("selections") or []:
        if not isinstance(raw_item, dict):
            continue
        media_id = _clean_text(raw_item.get("media_id"))
        candidate = by_id.get(media_id)
        if candidate is None:
            continue
        selected.append(
            MediaCandidate(
                media_id=candidate.media_id,
                path=candidate.path,
                media_type=candidate.media_type,
                captured_at=candidate.captured_at,
                parent_dir=candidate.parent_dir,
                title=candidate.title,
                short_summary=candidate.short_summary,
                searchable_text=candidate.searchable_text,
                score=_score_value(raw_item.get("score"), candidate.score),
                reason=_clean_text(raw_item.get("reason")) or "摘要扫描选中",
            )
        )
    return selected


def _merge_candidates(existing: list[MediaCandidate], incoming: list[MediaCandidate]) -> list[MediaCandidate]:
    by_id: dict[uuid.UUID, MediaCandidate] = {candidate.media_id: candidate for candidate in existing}
    order = [candidate.media_id for candidate in existing]
    for candidate in incoming:
        current = by_id.get(candidate.media_id)
        if current is None:
            by_id[candidate.media_id] = candidate
            order.append(candidate.media_id)
        elif candidate.score > current.score:
            by_id[candidate.media_id] = candidate
    return [by_id[media_id] for media_id in order]


def _pack_candidate_chunks(
    candidates: list[MediaCandidate],
    max_chars: int,
    *,
    text_limit: int,
) -> list[list[MediaCandidate]]:
    chunks: list[list[MediaCandidate]] = []
    current: list[MediaCandidate] = []
    current_size = 0
    for candidate in candidates:
        item_size = len(_candidate_text(candidate, text_limit=text_limit))
        if current and current_size + item_size > max_chars:
            chunks.append(current)
            current = []
            current_size = 0
        current.append(candidate)
        current_size += item_size
    if current:
        chunks.append(current)
    return chunks


def _candidates_text(candidates: list[MediaCandidate], *, text_limit: int) -> str:
    return "\n\n".join(_candidate_text(candidate, text_limit=text_limit) for candidate in candidates)


def _candidate_text(candidate: MediaCandidate, *, text_limit: int) -> str:
    captured_at = candidate.captured_at.isoformat() if candidate.captured_at else "unknown"
    text = _clip(candidate.searchable_text or candidate.short_summary or "", text_limit)
    return f"""ID: {candidate.media_id}
Type: {candidate.media_type}
Time: {captured_at}
Directory: {candidate.parent_dir or ""}
Path: {candidate.path}
Title: {candidate.title or ""}
Initial score: {candidate.score:.3f}
Initial reason: {candidate.reason}
Description:
{text}"""


def _directory_hints(db: Session) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    enabled_rules = effective_enabled_rules(list(db.scalars(select(DirectoryRule)).all()))
    for rule in enabled_rules:
        hints.append(
            {
                "name": _directory_name(rule.path),
                "path": rule.normalized_path,
                "display_path": rule.path,
                "count": 0,
            }
        )

    rows = db.execute(
        select(MediaFile.parent_dir, func.count(MediaFile.id), func.min(MediaFile.path))
        .where(visible_media_filter(db), MediaFile.parent_dir.is_not(None))
        .group_by(MediaFile.parent_dir)
        .order_by(func.count(MediaFile.id).desc())
        .limit(_DIRECTORY_HINT_LIMIT)
    ).all()
    seen = {item["path"] for item in hints}
    for parent_dir, count, sample_path in rows:
        if not parent_dir or parent_dir in seen:
            continue
        display_path = _display_parent_path(sample_path, parent_dir)
        hints.append(
            {
                "name": _directory_name(display_path),
                "path": parent_dir,
                "display_path": display_path,
                "count": int(count),
            }
        )
        seen.add(parent_dir)
    return hints[:_DIRECTORY_HINT_LIMIT]


def _resolve_directory_paths(directory_query: str, hints: list[dict[str, Any]]) -> list[str]:
    query = directory_query.strip().lower()
    if not query:
        return []
    normalized_query = normalize_path(directory_query).lower()
    matches: list[str] = []
    for item in hints:
        haystack = " ".join(
            [
                str(item.get("name", "")),
                str(item.get("path", "")),
                str(item.get("display_path", "")),
            ]
        ).lower()
        if query in haystack or normalized_query in haystack:
            matches.append(str(item["path"]))
    return matches


def _directories_note(directories: list[dict[str, Any]]) -> str:
    lines = [
        f"- {item.get('name')}: {item.get('display_path') or item.get('path')} ({item.get('count', 0)} 项)"
        for item in directories[:_DIRECTORY_HINT_LIMIT]
    ]
    return "可见目录：\n" + "\n".join(lines)


def _tool_catalog_text() -> str:
    return "\n".join(f"- {tool['name']}：{tool['description']}" for tool in AGENT_TOOLS)


def _history_text(history: list[SearchMessage]) -> str:
    if not history:
        return "无"
    lines: list[str] = []
    for message in history[-12:]:
        lines.append(f"{message.role}: {message.content}")
        blocks = message.blocks if isinstance(message.blocks, list) else []
        media_index = 1
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "media_grid":
                continue
            for item in block.get("items") or []:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    f"  展示媒体 {media_index}: id={item.get('media_id')} title={item.get('title')} path={item.get('path')}"
                )
                media_index += 1
    return "\n".join(lines)


def _blocks_to_text(blocks: list[dict[str, Any]]) -> str:
    parts = [str(block.get("text") or "") for block in blocks if block.get("type") == "text"]
    return "\n\n".join(part for part in parts if part.strip())


def _request_filter_payload(request: ChatStreamRequest) -> dict[str, Any]:
    return {
        "media_type": request.media_type,
        "directory_path": request.directory_path,
        "date_from": request.date_from.isoformat() if request.date_from else None,
        "date_to": request.date_to.isoformat() if request.date_to else None,
        "limit": request.limit,
        "candidate_k": request.candidate_k,
    }


def _directory_filter(directory_path: str):
    normalized = normalize_path(directory_path)
    return or_(
        MediaFile.parent_dir == normalized,
        MediaFile.parent_dir.like(f"{_escape_like(normalized)}/%", escape="\\"),
        MediaFile.root_path == normalized,
    )


def _display_parent_path(sample_path: str | None, fallback: str) -> str:
    if not sample_path:
        return fallback
    normalized = sample_path.replace("\\", "/").rstrip("/")
    if "/" not in normalized:
        return fallback
    return normalized.rsplit("/", 1)[0]


def _directory_name(path: str) -> str:
    normalized = path.replace("\\", "/").rstrip("/")
    if not normalized:
        return path
    if re.fullmatch(r"[A-Za-z]:", normalized):
        return normalized.upper()
    return normalized.rsplit("/", 1)[-1] or normalized


def _action_limit(action: dict[str, Any], default: int) -> int:
    try:
        value = int(action.get("limit") or default)
    except (TypeError, ValueError):
        value = default
    return max(1, min(1000, value))


def _score_value(value: object, default: float) -> float:
    try:
        return _clamp(float(value))
    except (TypeError, ValueError):
        return _clamp(default)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "none", "null", "unknown", "未知"}:
        return ""
    return text


def _clip(text: str, max_chars: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
