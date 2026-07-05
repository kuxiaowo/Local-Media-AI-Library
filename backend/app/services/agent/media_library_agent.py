from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, literal, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.path_utils import normalize_path
from app.models.db_models import EmbeddingProfile, MediaAiSummary, MediaEmbedding, MediaFile, SearchMessage
from app.models.schemas import ChatStreamRequest
from app.services.agent.context_builder import build_context_pack
from app.services.agent.response_composer import blocks_to_text, stream_blocks
from app.services.agent.types import AgentContextPack, AgentEvent, VisibleMemory
from app.services.agent.utils import (
    clamp_score,
    clean_text,
    clip_text,
    comparable_datetime,
    datetime_sort_key,
    directory_filter,
    jsonish,
    parse_datetime,
    parse_uuid_list,
)
from app.services.media_visibility import visible_media_filter
from app.services.ollama_client import OllamaClient
from app.services.search_rerank import keyword_score
from app.services.vector_math import cosine_similarity


ACTION_NAMES = {
    "answer_now",
    "list_directories",
    "list_directory_info",
    "list_media_descriptions",
    "search_descriptions",
    "select_media",
}
TOOL_ACTION_NAMES = ACTION_NAMES - {"answer_now"}
ANSWER_TYPES = {"answer", "summary", "media_selection"}
CONFIDENCE_VALUES = {"high", "medium", "low"}
RESPONSE_MODES = {"answer", "use_tool"}
DEFAULT_PAGE_SIZE = 40
MAX_PAGE_SIZE = 50
MAX_READ_PAGES_IN_CONTEXT = 8
MAX_CANDIDATES_IN_CONTEXT = 160
MAX_TOOL_EVENT_ITEMS = 20


MEDIA_LIBRARY_ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "response_mode": {"type": "string", "enum": sorted(RESPONSE_MODES)},
        "visible_response": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "answer_type": {"type": "string", "enum": sorted(ANSWER_TYPES)},
                "confidence": {"type": "string", "enum": sorted(CONFIDENCE_VALUES)},
                "checked_scope_summary": {"type": "string"},
                "limitations": {"type": "string"},
            },
            "required": ["text", "answer_type", "confidence", "checked_scope_summary", "limitations"],
        },
        "tool_request": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "enum": sorted(TOOL_ACTION_NAMES)},
                "reason_summary": {"type": "string"},
                "arguments": {"type": "object"},
            },
            "required": ["name", "reason_summary", "arguments"],
        },
        "media": {
            "type": "object",
            "properties": {
                "selected_media_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["selected_media_ids"],
        },
        "visible_memory_update": {
            "type": "object",
            "properties": {
                "known_facts": {"type": "array", "items": {"type": "string"}},
                "checked_scopes": {"type": "array", "items": {"type": "string"}},
                "candidate_media_ids": {"type": "array", "items": {"type": "string"}},
                "rejected_scopes": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["known_facts", "checked_scopes", "candidate_media_ids", "rejected_scopes"],
        },
    },
    "required": ["response_mode", "visible_response", "media", "visible_memory_update"],
}


MEDIA_LIBRARY_AGENT_SYSTEM_PROMPT = """你是本地媒体库 MediaLibraryAgent。
你只能基于 Context Pack 和工具返回的已有文本信息回答：媒体摘要、目录信息、时间信息、背景信息、视频分段文字。
你不能要求重新读取原图、视频、音频或缩略图，也不能假设自己看过原始媒体。
每轮只返回一个 JSON，不要输出 Markdown，不要输出长篇 chain-of-thought。
reason_summary 只写一句可见的简短理由。
visible_response.text 是给用户看的自然语言回答槽位；当 response_mode 是 answer 时必须写清楚回答、原因、范围或限制。

决策原则：
1. 先判断用户真正要什么，再判断当前 Context Pack 是否已经足够回答；足够时 response_mode 必须是 answer。但如果用户的目标是找图、推荐、筛选、选择、比较或展示具体媒体，answer 也应输出 media_selection 和 selected_media_ids，而不是纯文本。
2. 工具不是默认步骤。只有存在明确的信息缺口，并且该工具能补齐这个缺口时，才调用工具。
3. 如果 response_mode 是 use_tool，每轮选择成本最低、范围最窄、最能补齐缺口的 tool_request；不要为了“更完整”而搜索、分页或扩大范围。
4. 如果用户明确要求“直接回答/直接输出/自然语言输出/不要搜索/不要使用某工具”，必须尊重这个约束；response_mode 应为 answer，且不要输出 tool_request。
5. 概览、目录、统计、时间跨度、背景分布等结构化问题，优先使用 Context Pack、list_directories 或 list_directory_info。
6. 具体事件、画面内容、文本细节、人物动作等需要从大量媒体摘要中召回时，才使用 search_descriptions 或 list_media_descriptions。
7. 用户要找图、找视频、推荐、筛选、挑选“哪张/哪几个”、展示“这些/相关/匹配”的媒体，或问题天然需要给出可点击媒体结果时，最终 answer_type 应为 media_selection，并在 media.selected_media_ids 中填入最匹配的媒体 ID。只有目录结构、统计、概览、原因解释、时间线总结等不需要具体媒体结果的问题，才不要生成媒体卡片。
8. Agent 最多 {{max_agent_turns}} 轮；不要重复调用不能带来新信息的工具，信息不足时应使用 response_mode=answer 并说明检查范围和限制。

选择 response_mode 前先完成这个可见决策检查，但不要输出长篇推理：
- 用户是否禁止了某个工具或要求直接自然语言回答？如果是，遵守它。
- 当前 Context Pack 的哪些字段已经能回答问题？
- 还缺什么信息？缺口是否必须通过工具补齐？
- 如果没有必要的新信息缺口，response_mode 必须是 answer。

可用工具：
- list_directories：查看目录树，可传 query/page/page_size。
- list_directory_info：查看目录统计，可传 directory_path 或 directory_paths，也可传 query/page/page_size。
- list_media_descriptions：分页读取媒体描述，可传 directory_path/media_type/page/page_size/sort/date_from/date_to；page_size 最大 50。
- search_descriptions：基于已有摘要做关键词/向量检索，可传 query/directory_path/media_type/limit/date_from/date_to。
- select_media：从当前候选或已读描述中选择媒体，可传 media_ids。

每轮输出 JSON：
{
  "response_mode": "answer | use_tool",
  "visible_response": {
    "text": "给用户看的自然语言；use_tool 时可以为空，answer 时必须完整回答。",
    "answer_type": "answer | summary | media_selection",
    "confidence": "high | medium | low",
    "checked_scope_summary": "检查过哪些目录、时间范围、页码或候选。",
    "limitations": "如果信息不足，说明哪里不足。"
  },
  "tool_request": {
    "name": "list_directories | list_directory_info | list_media_descriptions | search_descriptions | select_media",
    "reason_summary": "简短说明为什么这样做，不要输出隐藏推理。",
    "arguments": {}
  },
  "media": {
    "selected_media_ids": []
  },
  "visible_memory_update": {
    "known_facts": [],
    "checked_scopes": [],
    "candidate_media_ids": [],
    "rejected_scopes": []
  }
}

当 response_mode 是 answer 时，不需要 tool_request，后端会直接输出 visible_response.text。
当 response_mode 是 use_tool 时，必须提供 tool_request。
当用户要找图、找视频、推荐、筛选、挑选、比较或展示具体媒体时，answer_type 用 media_selection，media.selected_media_ids 必须填入已读描述或候选中的媒体 ID；不要只用自然语言描述候选。
选择媒体时 selected_media_ids 只能来自 Context Pack 的 read_description_pages 或 candidate_media。最终媒体卡片的数量、内容和顺序完全按你返回的 selected_media_ids 执行；后端不会按向量分数补齐或重排。
如果媒体太多，不要一次性假装已读完；先分页读取或搜索，再更新 visible_memory。
只返回符合 schema 的 JSON。"""


@dataclass(frozen=True)
class MediaAgentAction:
    response_mode: str
    action: str
    reason_summary: str
    arguments: dict[str, Any]
    visible_response: dict[str, Any]
    media: dict[str, Any]
    visible_memory_update: VisibleMemory


@dataclass(frozen=True)
class FinalAnswer:
    answer_type: str
    answer: str
    selected_media_ids: list[str]
    confidence: str
    checked_scope_summary: str
    limitations: str

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "answer_type": self.answer_type,
            "answer": self.answer,
            "confidence": self.confidence,
            "checked_scope_summary": self.checked_scope_summary,
            "limitations": self.limitations,
        }
        if self.selected_media_ids:
            payload["selected_media_ids"] = self.selected_media_ids
        return payload


async def run_media_library_agent_turn_events(
    db: Session,
    request: ChatStreamRequest,
    history: list[SearchMessage],
    ollama: OllamaClient,
) -> Any:
    settings = get_settings()
    model_name = settings.default_ai_search_model.strip() or settings.default_summary_model
    max_agent_turns = _agent_max_turns(settings)
    base_context = build_context_pack(db, request, history)
    db.close()
    visible_memory = base_context.visible_memory
    read_pages: list[dict[str, Any]] = []
    candidate_media: list[dict[str, Any]] = []

    for turn in range(1, max_agent_turns + 1):
        context_pack = replace(
            base_context,
            visible_memory=visible_memory,
            read_description_pages=read_pages[-MAX_READ_PAGES_IN_CONTEXT:],
            candidate_media=candidate_media[:MAX_CANDIDATES_IN_CONTEXT],
        )
        try:
            action = await _ask_agent_action(
                ollama=ollama,
                model_name=model_name,
                request=request,
                context_pack=context_pack,
                max_agent_turns=max_agent_turns,
            )
        except Exception as exc:
            async for event in _fallback_events(
                db=db,
                ollama=ollama,
                request=request,
                model_name=model_name,
                reason=f"模型 Agent JSON 失败，已回退到关键词/向量检索：{exc}",
            ):
                yield event
            return

        if (
            action.action == "answer_now"
            and _is_media_selection_request(request.message)
            and not _uuid_string_list(action.media.get("selected_media_ids") if isinstance(action.media, dict) else None, limit=1)
            and not candidate_media
            and not read_pages
        ):
            action = _forced_media_lookup_action(request)

        visible_memory = _merge_visible_memory(visible_memory, action.visible_memory_update)
        yield AgentEvent(
            "agent_action",
            {
                "turn": turn,
                "response_mode": action.response_mode,
                "action": action.action,
                "tool": action.action,
                "reason_summary": action.reason_summary,
                "summary": action.reason_summary,
                "arguments": _public_arguments(action.arguments),
                "visible_response": _public_visible_response(action.visible_response),
                "media": _public_media_selection(action.media),
                "visible_memory": visible_memory.to_prompt_payload(),
            },
        )

        if action.action == "answer_now":
            final_answer = _normalize_final_answer(
                _final_answer_payload_from_action(action),
                allowed_media_ids=_known_media_ids(read_pages, candidate_media),
            )
            selected = _candidate_payloads_for_ids(db, final_answer.selected_media_ids, candidate_media, read_pages)
            final_answer = replace(final_answer, selected_media_ids=[item["media_id"] for item in selected])
            blocks = _blocks_from_final_answer(final_answer, selected)
            db.close()
            yield AgentEvent("visible_memory", {"visible_memory": visible_memory.to_prompt_payload()})
            yield AgentEvent("final_answer", {"final_answer": final_answer.to_payload()})
            async for event in stream_blocks(blocks):
                yield event
            yield _assistant_message(blocks, model_name, candidate_count=len(candidate_media))
            return

        try:
            result = await _execute_action(db, ollama, request, action)
            db.close()
        except Exception as exc:
            db.close()
            async for event in _fallback_events(
                db=db,
                ollama=ollama,
                request=request,
                model_name=model_name,
                reason=f"执行 {action.action} 失败，已回退到关键词/向量检索：{exc}",
            ):
                yield event
            return

        if result.get("read_page"):
            read_pages.append(result["read_page"])
        if result.get("candidate_media"):
            candidate_media = _merge_candidate_payloads(candidate_media, result["candidate_media"])
            visible_memory = _merge_visible_memory(
                visible_memory,
                VisibleMemory(candidate_media_ids=[item["media_id"] for item in candidate_media[:MAX_CANDIDATES_IN_CONTEXT]]),
            )

        yield AgentEvent(
            "tool_result",
            {
                "tool": action.action,
                "summary": result.get("summary") or "工具已返回。",
                "result": _tool_event_result(result.get("result")),
                "visible_memory": visible_memory.to_prompt_payload(),
            },
        )

    async for event in _fallback_events(
        db=db,
        ollama=ollama,
        request=request,
        model_name=model_name,
        reason="Agent 已达到最大轮数，停止继续扩展检索。",
        allow_media_search=False,
    ):
        yield event


async def _ask_agent_action(
    *,
    ollama: OllamaClient,
    model_name: str,
    request: ChatStreamRequest,
    context_pack: AgentContextPack,
    max_agent_turns: int,
) -> MediaAgentAction:
    prompt = f"""用户原始问题：
{request.message}

Context Pack：
{json.dumps(context_pack.planner_payload(), ensure_ascii=False, default=str)}

当前 Agent 最大轮数：{max_agent_turns}

请先判断当前 Context Pack 是否已经足够回答，不要默认搜索或分页；只有存在必须补齐的信息缺口时才调用工具。请返回一个 JSON envelope：response_mode、visible_response、tool_request、media、visible_memory_update。"""
    raw = await ollama.generate_text_json(
        model=model_name,
        prompt=prompt,
        schema=MEDIA_LIBRARY_ACTION_SCHEMA,
        system_prompt=_agent_system_prompt(max_agent_turns),
    )
    return _normalize_action(raw)


def _agent_system_prompt(max_agent_turns: int) -> str:
    return MEDIA_LIBRARY_AGENT_SYSTEM_PROMPT.replace("{{max_agent_turns}}", str(max_agent_turns))


def _agent_max_turns(settings: Any) -> int:
    try:
        turns = int(getattr(settings, "ai_search_max_turns", 8))
    except (TypeError, ValueError):
        turns = 8
    return max(1, min(30, turns))


def _normalize_action(raw: dict[str, Any]) -> MediaAgentAction:
    visible_response = raw.get("visible_response")
    if not isinstance(visible_response, dict):
        visible_response = {}
    media = raw.get("media")
    if not isinstance(media, dict):
        media = {}
    memory = raw.get("visible_memory_update")
    if not isinstance(memory, dict):
        memory = {}

    response_mode = clean_text(raw.get("response_mode")).lower()
    if response_mode in RESPONSE_MODES:
        if response_mode == "answer":
            action = "answer_now"
            arguments: dict[str, Any] = {}
            reason_summary = clean_text(raw.get("reason_summary")) or "直接回答"
        else:
            tool_request = raw.get("tool_request")
            if not isinstance(tool_request, dict):
                tool_request = {}
            action = clean_text(tool_request.get("name"))
            arguments = tool_request.get("arguments")
            reason_summary = clean_text(tool_request.get("reason_summary")) or clean_text(raw.get("reason_summary"))
            if action not in TOOL_ACTION_NAMES:
                raise ValueError("Agent tool_request is missing or invalid")
            if not isinstance(arguments, dict):
                arguments = {}
    else:
        action_node = raw.get("action")
        if isinstance(action_node, dict):
            action = clean_text(action_node.get("name") or action_node.get("action"))
            arguments = action_node.get("arguments")
            reason_summary = clean_text(action_node.get("reason_summary")) or clean_text(raw.get("reason_summary"))
        else:
            action = clean_text(action_node)
            arguments = raw.get("arguments")
            reason_summary = clean_text(raw.get("reason_summary"))
        if action not in ACTION_NAMES:
            raise ValueError("Agent action is missing or invalid")
        if not isinstance(arguments, dict):
            arguments = {}
        response_mode = "answer" if action == "answer_now" else "use_tool"

    return MediaAgentAction(
        response_mode=response_mode,
        action=action,
        reason_summary=clip_text(reason_summary or f"执行 {action}", 160),
        arguments=arguments,
        visible_response=visible_response,
        media=media,
        visible_memory_update=VisibleMemory(
            known_facts=_string_list(memory.get("known_facts"), limit=20),
            checked_scopes=_string_list(memory.get("checked_scopes"), limit=20),
            candidate_media_ids=_uuid_string_list(memory.get("candidate_media_ids"), limit=100),
            rejected_scopes=_string_list(memory.get("rejected_scopes"), limit=20),
        ),
    )


def _is_media_selection_request(message: str) -> bool:
    text = clean_text(message)
    if not text:
        return False
    has_media_noun = any(word in text for word in ("照片", "图片", "图像", "相片", "视频", "影片", "短片", "媒体", "封面", "壁纸"))
    has_selection_intent = any(
        word in text
        for word in (
            "找",
            "查找",
            "搜",
            "搜索",
            "推荐",
            "筛选",
            "挑",
            "选",
            "哪张",
            "哪几张",
            "哪几个",
            "展示",
            "显示",
            "给我看",
            "列出",
            "卡片",
        )
    )
    if has_media_noun and has_selection_intent:
        return True
    return any(pattern in text for pattern in ("找一张", "找几张", "找几段", "推荐几张", "推荐几个", "给我几张"))


def _forced_media_lookup_action(request: ChatStreamRequest) -> MediaAgentAction:
    arguments: dict[str, Any] = {
        "query": request.message,
        "limit": request.limit,
    }
    if request.media_type in {"image", "video"}:
        arguments["media_type"] = request.media_type
    if request.directory_path:
        arguments["directory_path"] = request.directory_path
    if request.date_from is not None:
        arguments["date_from"] = request.date_from.isoformat()
    if request.date_to is not None:
        arguments["date_to"] = request.date_to.isoformat()
    return MediaAgentAction(
        response_mode="use_tool",
        action="search_descriptions",
        reason_summary="用户要找具体媒体，先检索已有媒体摘要。",
        arguments=arguments,
        visible_response={
            "text": "",
            "answer_type": "media_selection",
            "confidence": "medium",
            "checked_scope_summary": "",
            "limitations": "",
        },
        media={"selected_media_ids": []},
        visible_memory_update=VisibleMemory(),
    )


async def _execute_action(
    db: Session,
    ollama: OllamaClient,
    request: ChatStreamRequest,
    action: MediaAgentAction,
) -> dict[str, Any]:
    if action.action == "list_directories":
        return _list_directories(build_context_pack(db, request, []), action.arguments)
    if action.action == "list_directory_info":
        return _list_directory_info(build_context_pack(db, request, []), action.arguments)
    if action.action == "list_media_descriptions":
        return _list_media_descriptions(db, request, action.arguments)
    if action.action == "search_descriptions":
        return {
            **await _search_descriptions(db, ollama, request, action.arguments),
            "read_page": None,
        }
    if action.action == "select_media":
        return _select_media(db, action.arguments)
    raise ValueError(f"Unsupported action: {action.action}")


def _list_directories(context_pack: AgentContextPack, arguments: dict[str, Any]) -> dict[str, Any]:
    query = clean_text(arguments.get("query")).lower()
    page, page_size = _page_args(arguments)
    nodes = context_pack.directory_tree
    if query:
        nodes = [node for node in nodes if query in f"{node.get('path')} {node.get('name')}".lower()]
    page_items, total = _page_slice(nodes, page=page, page_size=page_size)
    result = {"page": page, "page_size": page_size, "total": total, "items": page_items}
    return {
        "summary": f"返回目录 {len(page_items)}/{total} 个。",
        "result": result,
    }


def _list_directory_info(context_pack: AgentContextPack, arguments: dict[str, Any]) -> dict[str, Any]:
    paths = _string_list(arguments.get("directory_paths"), limit=50)
    single = clean_text(arguments.get("directory_path"))
    if single:
        paths.append(single)
    normalized_paths = {normalize_path(path) for path in paths if path}
    query = clean_text(arguments.get("query")).lower()
    items = context_pack.directory_stats
    if normalized_paths:
        items = [item for item in items if normalize_path(item.get("path")) in normalized_paths]
    elif query:
        items = [item for item in items if query in clean_text(item.get("path")).lower()]
    page, page_size = _page_args(arguments)
    page_items, total = _page_slice(items, page=page, page_size=page_size)
    result = {"page": page, "page_size": page_size, "total": total, "items": page_items}
    return {
        "summary": f"返回目录统计 {len(page_items)}/{total} 条。",
        "result": result,
    }


def _list_media_descriptions(db: Session, request: ChatStreamRequest, arguments: dict[str, Any]) -> dict[str, Any]:
    page, page_size = _page_args(arguments, default_page_size=DEFAULT_PAGE_SIZE)
    stmt = _media_description_stmt(db, request, arguments)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = db.execute(_apply_sort(stmt, clean_text(arguments.get("sort"))).offset((page - 1) * page_size).limit(page_size)).all()
    items = [_media_description_payload(media, summary) for media, summary in rows]
    result = {
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_next": page * page_size < total,
        "items": items,
    }
    read_page = {
        "directory_path": clean_text(arguments.get("directory_path")) or request.directory_path,
        "media_type": clean_text(arguments.get("media_type")) or request.media_type,
        "page": page,
        "page_size": page_size,
        "sort": clean_text(arguments.get("sort")) or "captured_desc",
        "total": total,
        "items": items,
    }
    return {
        "summary": f"读取媒体描述 {len(items)}/{total} 条，第 {page} 页。",
        "result": result,
        "read_page": read_page,
        "candidate_media": [_candidate_from_description(item, score=0.5, reason="已分页读取描述") for item in items],
    }


async def _search_descriptions(
    db: Session,
    ollama: OllamaClient,
    request: ChatStreamRequest,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    query = clean_text(arguments.get("query")) or request.message
    limit = _int_arg(arguments.get("limit"), default=max(request.limit, 30), minimum=1, maximum=max(200, request.limit))
    candidates = await _search_description_candidates(
        db=db,
        ollama=ollama,
        request=request,
        query=query,
        arguments=arguments,
        limit=limit,
    )
    result = {"query": query, "total": len(candidates), "items": candidates}
    return {
        "summary": f"检索已有描述，返回 {len(candidates)} 个候选。",
        "result": result,
        "candidate_media": candidates,
    }


def _select_media(db: Session, arguments: dict[str, Any]) -> dict[str, Any]:
    media_ids = parse_uuid_list(arguments.get("media_ids"))
    items = _load_candidate_payloads(db, [str(media_id) for media_id in media_ids])
    result = {"items": items}
    return {
        "summary": f"选择并校验 {len(items)} 个媒体。",
        "result": result,
        "candidate_media": items,
    }


async def _fallback_events(
    *,
    db: Session,
    ollama: OllamaClient,
    request: ChatStreamRequest,
    model_name: str,
    reason: str,
    allow_media_search: bool = True,
) -> Any:
    fallback_tool = "fallback_keyword_vector_search" if allow_media_search else "fallback_no_media_selection"
    yield AgentEvent(
        "agent_action",
        {
            "turn": 0,
            "action": "fallback",
            "tool": fallback_tool,
            "reason_summary": clip_text(reason, 220),
            "summary": clip_text(reason, 220),
            "arguments": {},
            "visible_memory": VisibleMemory().to_prompt_payload(),
        },
    )
    candidates = (
        await _search_description_candidates(
            db=db,
            ollama=ollama,
            request=request,
            query=request.message,
            arguments={},
            limit=request.limit,
        )
        if allow_media_search
        else []
    )
    scoped_explanation = _no_media_scope_explanation(db, request) if not allow_media_search else None
    fallback_answer = (
        "模型没有返回可用的 Agent JSON；已检索到候选媒体，但不会用向量排序直接生成媒体卡片。请重试一次，让 Agent 读取候选后再选择要展示的媒体。"
        if candidates
        else "没有找到符合当前问题范围的已分析媒体。"
    )
    if scoped_explanation is not None:
        fallback_answer = scoped_explanation["answer"]
    final = FinalAnswer(
        answer_type="answer",
        answer=fallback_answer,
        selected_media_ids=[],
        confidence="low",
        checked_scope_summary=(
            f"回退检索检查了已分析媒体摘要，返回 {len(candidates)} 个候选。"
            if allow_media_search
            else scoped_explanation["checked_scope_summary"] if scoped_explanation is not None else "已停止继续扩展检索，避免输出与用户时间范围不一致的媒体。"
        ),
        limitations=(
            "这是 fallback 结果，只基于已有摘要和向量/关键词分数；为避免由向量决定最终卡片，未自动选择媒体。"
            if allow_media_search
            else scoped_explanation["limitations"] if scoped_explanation is not None else "如果该时间段确实没有已扫描并完成 AI 摘要的媒体，Agent 无法回答具体发生了什么。"
        ),
    )
    blocks = _blocks_from_final_answer(final, [])
    db.close()
    yield AgentEvent(
        "tool_result",
        {
            "tool": fallback_tool,
            "summary": final.checked_scope_summary,
            "result": _tool_event_result({"items": candidates}),
            "visible_memory": VisibleMemory(candidate_media_ids=final.selected_media_ids).to_prompt_payload(),
        },
    )
    yield AgentEvent("final_answer", {"final_answer": final.to_payload()})
    async for event in stream_blocks(blocks):
        yield event
    yield _assistant_message(blocks, model_name, candidate_count=len(candidates))


async def _search_description_candidates(
    *,
    db: Session,
    ollama: OllamaClient,
    request: ChatStreamRequest,
    query: str,
    arguments: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    profile_id = _embedding_profile_id(db)
    if profile_id is not None:
        stmt = (
            select(MediaFile, MediaAiSummary, MediaEmbedding.embedding)
            .join(MediaAiSummary, MediaAiSummary.media_id == MediaFile.id)
            .outerjoin(
                MediaEmbedding,
                (MediaEmbedding.media_id == MediaFile.id) & (MediaEmbedding.profile_id == profile_id),
            )
        )
    else:
        stmt = select(MediaFile, MediaAiSummary, literal(None)).join(
            MediaAiSummary, MediaAiSummary.media_id == MediaFile.id
        )
    stmt = _apply_media_filters(db, stmt, request, arguments)
    rows = db.execute(stmt).all()
    db.close()
    query_vector = await _query_vector(db, ollama, query) if rows else None
    scored: list[tuple[float, dict[str, Any]]] = []
    for media, summary, embedding in rows:
        vector = 0.0
        if query_vector is not None and embedding:
            vector = max(0.0, min(1.0, cosine_similarity(query_vector, embedding)))
        keyword = keyword_score(query, _searchable_text(summary))
        score = 0.7 * vector + 0.3 * keyword if query_vector is not None else keyword
        if score <= 0 and query:
            score = 0.001
        reason = f"回退/工具检索：vector={vector:.3f}, keyword={keyword:.3f}"
        scored.append((score, _candidate_from_row(media, summary, score=score, reason=reason)))
    scored.sort(key=lambda item: (item[0], datetime_sort_key(_parse_iso(item[1].get("captured_at")))), reverse=True)
    return [item for _score, item in scored[:limit]]


def _no_media_scope_explanation(db: Session, request: ChatStreamRequest) -> dict[str, str]:
    date_from, date_to = _explicit_date_range_from_message(request.message)
    date_from = request.date_from or date_from
    date_to = request.date_to or date_to
    media_type = request.media_type if request.media_type in {"image", "video"} else "any"
    directory_path = clean_text(request.directory_path)

    stmt = (
        select(func.count(MediaFile.id))
        .join(MediaAiSummary, MediaAiSummary.media_id == MediaFile.id)
        .where(MediaFile.status == "done", visible_media_filter(db))
    )
    if media_type != "any":
        stmt = stmt.where(MediaFile.media_type == media_type)
    if directory_path:
        stmt = stmt.where(directory_filter(directory_path))
    if date_from is not None:
        stmt = stmt.where(MediaFile.captured_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(MediaFile.captured_at <= date_to)
    scoped_count = int(db.scalar(stmt) or 0)

    overview = _library_time_overview(db)
    range_text = _scope_range_text(date_from, date_to)
    media_type_text = {"image": "图片", "video": "视频", "any": "媒体"}[media_type]
    directory_text = f"目录 `{directory_path}` 中的" if directory_path else "媒体库中的"
    overview_text = ""
    if overview["media_count"] > 0:
        overview_text = (
            f"当前可回答媒体总数为 {overview['media_count']}，"
            f"已解析拍摄时间范围约为 {overview['earliest'] or '未知'} 到 {overview['latest'] or '未知'}。"
        )
    else:
        overview_text = "当前媒体库里还没有可用于回答的已完成 AI 摘要媒体。"

    if scoped_count > 0:
        answer = (
            f"我没有给出媒体卡片，因为 Agent 多轮检查后没有形成可靠选择；"
            f"不过按当前范围{range_text}能查到 {scoped_count} 条已分析{media_type_text}。"
            "这通常说明模型没有在限定轮数内完成归纳，而不是数据库完全没有该范围记录。"
        )
        checked = f"按当前过滤条件{range_text}检查到 {scoped_count} 条已完成 AI 摘要的{media_type_text}。"
        limitations = "未返回媒体卡片是为了避免把不可靠候选当成最终结果；可以继续追问“只总结这些记录”或缩小目录/关键词。"
    else:
        answer = (
            f"我没有找到{directory_text}{range_text}已完成 AI 摘要的{media_type_text}，所以不能可靠回答那段时间发生了什么，"
            "也不会把其他年份的相似照片当成结果。\n"
            f"{overview_text}\n"
            "可能原因：这段时间的媒体没有导入或扫描；媒体还没有生成 AI 摘要；文件缺少可解析拍摄时间；"
            "或者实际拍摄时间不在你指定的范围内。"
        )
        checked = f"按当前过滤条件{range_text}检查到 0 条已完成 AI 摘要的{media_type_text}；{overview_text}"
        limitations = "这里只基于数据库已有摘要和 captured_at 时间字段判断；没有重新读取原图/视频，也没有扫描磁盘查找未入库文件。"

    return {
        "answer": answer,
        "checked_scope_summary": checked,
        "limitations": limitations,
    }


def _library_time_overview(db: Session) -> dict[str, Any]:
    row = db.execute(
        select(
            func.count(MediaFile.id),
            func.min(MediaFile.captured_at),
            func.max(MediaFile.captured_at),
        )
        .join(MediaAiSummary, MediaAiSummary.media_id == MediaFile.id)
        .where(MediaFile.status == "done", visible_media_filter(db))
    ).one()
    count, earliest, latest = row
    earliest = comparable_datetime(earliest)
    latest = comparable_datetime(latest)
    return {
        "media_count": int(count or 0),
        "earliest": earliest.date().isoformat() if earliest else None,
        "latest": latest.date().isoformat() if latest else None,
    }


def _scope_range_text(date_from: datetime | None, date_to: datetime | None) -> str:
    if date_from is None and date_to is None:
        return "内"
    if date_from is not None and date_to is not None:
        return f"在 {date_from.date().isoformat()} 到 {date_to.date().isoformat()} 之间的"
    if date_from is not None:
        return f"在 {date_from.date().isoformat()} 之后的"
    return f"在 {date_to.date().isoformat()} 之前的"


def _media_description_stmt(db: Session, request: ChatStreamRequest, arguments: dict[str, Any]):
    stmt = select(MediaFile, MediaAiSummary).join(MediaAiSummary, MediaAiSummary.media_id == MediaFile.id)
    return _apply_media_filters(db, stmt, request, arguments)


def _apply_media_filters(db: Session, stmt, request: ChatStreamRequest, arguments: dict[str, Any]):
    stmt = stmt.where(MediaFile.status == "done", visible_media_filter(db))
    media_type = clean_text(arguments.get("media_type")) or request.media_type
    if media_type in {"image", "video"}:
        stmt = stmt.where(MediaFile.media_type == media_type)
    directory_path = clean_text(arguments.get("directory_path")) or clean_text(request.directory_path)
    if directory_path:
        stmt = stmt.where(directory_filter(directory_path))
    inferred_date_from, inferred_date_to = _explicit_date_range_from_message(request.message)
    date_from = parse_datetime(arguments.get("date_from")) or request.date_from or inferred_date_from
    date_to = parse_datetime(arguments.get("date_to"), end_of_day=True) or request.date_to or inferred_date_to
    if date_from is not None:
        stmt = stmt.where(MediaFile.captured_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(MediaFile.captured_at <= date_to)
    return stmt


def _apply_sort(stmt, sort: str):
    if sort == "captured_asc":
        return stmt.order_by(MediaFile.captured_at.is_(None), MediaFile.captured_at.asc(), MediaFile.created_at.asc())
    if sort == "path_asc":
        return stmt.order_by(MediaFile.parent_dir.asc(), MediaFile.path.asc())
    if sort == "created_desc":
        return stmt.order_by(MediaFile.created_at.desc())
    return stmt.order_by(MediaFile.captured_at.is_(None), MediaFile.captured_at.desc(), MediaFile.created_at.desc())


def _media_description_payload(media: MediaFile, summary: MediaAiSummary) -> dict[str, Any]:
    return {
        "media_id": str(media.id),
        "path": media.path,
        "parent_dir": media.parent_dir,
        "root_path": media.root_path,
        "media_type": media.media_type,
        "captured_at": media.captured_at.isoformat() if media.captured_at else None,
        "width": media.width,
        "height": media.height,
        "duration_seconds": media.duration_seconds,
        "background_context": clip_text(media.background_context, 500),
        "title": summary.title,
        "short_summary": summary.short_summary,
        "detailed_summary": clip_text(summary.detailed_summary, 1000),
        "scene": summary.scene,
        "objects": jsonish(summary.objects),
        "people": jsonish(summary.people),
        "actions": jsonish(summary.actions),
        "text_visible": jsonish(summary.text_visible),
        "location_guess": summary.location_guess,
        "time_clues": summary.time_clues,
        "mood": summary.mood,
        "search_keywords": jsonish(summary.search_keywords),
        "searchable_text": clip_text(summary.searchable_text, 1400),
        "confidence": summary.confidence,
    }


def _candidate_from_description(item: dict[str, Any], *, score: float, reason: str) -> dict[str, Any]:
    return {
        "media_id": clean_text(item.get("media_id")),
        "path": item.get("path"),
        "thumbnail_url": f"/api/media/{item.get('media_id')}/thumbnail",
        "media_type": item.get("media_type"),
        "captured_at": item.get("captured_at"),
        "title": item.get("title"),
        "short_summary": item.get("short_summary"),
        "match_reason": reason,
        "score": clamp_score(score),
        "parent_dir": item.get("parent_dir"),
        "searchable_text": clip_text(clean_text(item.get("searchable_text")), 800),
    }


def _candidate_from_row(
    media: MediaFile,
    summary: MediaAiSummary,
    *,
    score: float,
    reason: str,
) -> dict[str, Any]:
    return {
        "media_id": str(media.id),
        "path": media.path,
        "thumbnail_url": f"/api/media/{media.id}/thumbnail",
        "media_type": media.media_type,
        "captured_at": media.captured_at.isoformat() if media.captured_at else None,
        "title": summary.title,
        "short_summary": summary.short_summary,
        "match_reason": reason,
        "score": clamp_score(score),
        "parent_dir": media.parent_dir,
        "root_path": media.root_path,
        "searchable_text": clip_text(summary.searchable_text, 800),
    }


def _searchable_text(summary: MediaAiSummary) -> str:
    keywords = summary.search_keywords
    if isinstance(keywords, list):
        keyword_text = " ".join(str(item) for item in keywords)
    elif isinstance(keywords, dict):
        keyword_text = " ".join(str(item) for item in list(keywords.keys()) + list(keywords.values()))
    else:
        keyword_text = ""
    return " ".join(
        part
        for part in [
            summary.title,
            summary.short_summary,
            summary.detailed_summary,
            summary.scene,
            summary.searchable_text,
            keyword_text,
        ]
        if part
    )


async def _query_vector(db: Session, ollama: OllamaClient, query: str) -> list[float] | None:
    model_name = get_settings().default_embedding_model.strip()
    profile_id = _embedding_profile_id(db) if model_name else None
    db.close()
    if not model_name or profile_id is None:
        return None
    try:
        return await ollama.embed_text(model=model_name, text=query)
    except Exception:
        return None


def _embedding_profile_id(db: Session):
    model_name = get_settings().default_embedding_model.strip()
    if not model_name:
        return None
    return db.scalar(select(EmbeddingProfile.id).where(EmbeddingProfile.model_name == model_name))


def _final_answer_payload_from_action(action: MediaAgentAction) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    legacy = action.arguments.get("final_answer") if isinstance(action.arguments.get("final_answer"), dict) else action.arguments
    if isinstance(legacy, dict):
        payload.update(legacy)

    visible = action.visible_response if isinstance(action.visible_response, dict) else {}
    text = clean_text(visible.get("text")) or clean_text(visible.get("answer"))
    if text:
        payload["answer"] = text
    for key in ("answer_type", "confidence", "checked_scope_summary", "limitations"):
        value = clean_text(visible.get(key))
        if value:
            payload[key] = value

    media_ids = action.media.get("selected_media_ids") if isinstance(action.media, dict) else None
    if isinstance(media_ids, list):
        payload["selected_media_ids"] = media_ids
    elif isinstance(visible.get("selected_media_ids"), list):
        payload["selected_media_ids"] = visible["selected_media_ids"]
    return payload


def _normalize_final_answer(raw: dict[str, Any], *, allowed_media_ids: set[str]) -> FinalAnswer:
    payload = raw.get("final_answer") if isinstance(raw.get("final_answer"), dict) else raw
    if isinstance(raw.get("visible_response"), dict):
        payload = dict(payload)
        visible = raw["visible_response"]
        text = clean_text(visible.get("text")) or clean_text(visible.get("answer"))
        if text:
            payload["answer"] = text
        for key in ("answer_type", "confidence", "checked_scope_summary", "limitations"):
            value = clean_text(visible.get(key))
            if value:
                payload[key] = value
    if isinstance(raw.get("media"), dict) and isinstance(raw["media"].get("selected_media_ids"), list):
        payload = dict(payload)
        payload["selected_media_ids"] = raw["media"]["selected_media_ids"]
    answer_type = clean_text(payload.get("answer_type"))
    if answer_type not in ANSWER_TYPES:
        answer_type = "answer"
    selected = _uuid_string_list(payload.get("selected_media_ids"), limit=100)
    if selected and answer_type != "media_selection":
        answer_type = "media_selection"
    if answer_type != "media_selection":
        selected = []
    selected = [media_id for media_id in selected if media_id in allowed_media_ids] if allowed_media_ids else []
    confidence = clean_text(payload.get("confidence")).lower()
    if confidence not in CONFIDENCE_VALUES:
        confidence = "medium"
    checked_scope_summary = clip_text(clean_text(payload.get("checked_scope_summary")), 600)
    limitations = clip_text(clean_text(payload.get("limitations")), 600)
    answer = clean_text(payload.get("answer"))
    if not answer:
        details = []
        if checked_scope_summary:
            details.append(f"已检查：{checked_scope_summary}")
        if limitations:
            details.append(f"限制：{limitations}")
        answer = "没有得到可直接回答的结论。" + ("\n" + "\n".join(details) if details else "")
    return FinalAnswer(
        answer_type=answer_type,
        answer=answer,
        selected_media_ids=selected,
        confidence=confidence,
        checked_scope_summary=checked_scope_summary,
        limitations=limitations,
    )


def _blocks_from_final_answer(final_answer: FinalAnswer, selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if final_answer.answer_type == "summary":
        blocks.append({"type": "summary", "title": "范围总结", "text": final_answer.answer})
    else:
        blocks.append({"type": "text", "text": final_answer.answer})
    notes = []
    if final_answer.checked_scope_summary:
        notes.append(f"检查范围：{final_answer.checked_scope_summary}")
    if final_answer.limitations:
        notes.append(f"限制：{final_answer.limitations}")
    if notes:
        blocks.append({"type": "text", "text": "\n".join(notes)})
    if selected and final_answer.answer_type == "media_selection":
        blocks.append({"type": "media_grid", "title": "匹配媒体", "items": [_public_media_item(item) for item in selected]})
    return blocks


def _public_media_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "media_id": clean_text(item.get("media_id")),
        "path": clean_text(item.get("path")),
        "thumbnail_url": clean_text(item.get("thumbnail_url")) or f"/api/media/{item.get('media_id')}/thumbnail",
        "media_type": clean_text(item.get("media_type")) or "image",
        "captured_at": item.get("captured_at"),
        "title": item.get("title"),
        "short_summary": item.get("short_summary"),
        "match_reason": clean_text(item.get("match_reason")) or "基于已有摘要判断相关",
        "score": clamp_score(item.get("score"), 0.5),
    }


def _assistant_message(blocks: list[dict], model_name: str, *, candidate_count: int) -> AgentEvent:
    return AgentEvent(
        "assistant_message",
        {
            "content": blocks_to_text(blocks) or "已完成。",
            "blocks": blocks,
            "ai_model": model_name,
            "candidate_count": candidate_count,
        },
    )


def _candidate_payloads_for_ids(
    db: Session,
    media_ids: list[str],
    candidate_media: list[dict[str, Any]],
    read_pages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for item in candidate_media:
        media_id = clean_text(item.get("media_id"))
        if media_id:
            by_id[media_id] = item
    for page in read_pages:
        for item in page.get("items") or []:
            if isinstance(item, dict):
                candidate = _candidate_from_description(item, score=0.6, reason="已读描述页中的媒体")
                by_id.setdefault(candidate["media_id"], candidate)
    missing = [media_id for media_id in media_ids if media_id not in by_id]
    for item in _load_candidate_payloads(db, missing):
        by_id[item["media_id"]] = item
    return [by_id[media_id] for media_id in media_ids if media_id in by_id]


def _load_candidate_payloads(db: Session, media_ids: list[str]) -> list[dict[str, Any]]:
    parsed = parse_uuid_list(media_ids)
    if not parsed:
        return []
    rows = db.execute(
        select(MediaFile, MediaAiSummary)
        .join(MediaAiSummary, MediaAiSummary.media_id == MediaFile.id)
        .where(MediaFile.id.in_(parsed), MediaFile.status == "done", visible_media_filter(db))
    ).all()
    by_id = {
        str(media.id): _candidate_from_row(media, summary, score=0.7, reason="已校验媒体 ID")
        for media, summary in rows
    }
    return [by_id[str(media_id)] for media_id in parsed if str(media_id) in by_id]


def _known_media_ids(read_pages: list[dict[str, Any]], candidate_media: list[dict[str, Any]]) -> set[str]:
    ids = {clean_text(item.get("media_id")) for item in candidate_media if isinstance(item, dict)}
    for page in read_pages:
        for item in page.get("items") or []:
            if isinstance(item, dict):
                ids.add(clean_text(item.get("media_id")))
    return {media_id for media_id in ids if media_id}


def _merge_candidate_payloads(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in existing + incoming:
        media_id = clean_text(item.get("media_id")) if isinstance(item, dict) else ""
        if not media_id:
            continue
        current = by_id.get(media_id)
        if current is None:
            by_id[media_id] = item
            order.append(media_id)
        elif clamp_score(item.get("score")) > clamp_score(current.get("score")):
            by_id[media_id] = item
    return [by_id[media_id] for media_id in order][:MAX_CANDIDATES_IN_CONTEXT]


def _merge_visible_memory(current: VisibleMemory, update: VisibleMemory) -> VisibleMemory:
    return VisibleMemory(
        known_facts=_dedupe_strings(current.known_facts + update.known_facts, limit=40),
        checked_scopes=_dedupe_strings(current.checked_scopes + update.checked_scopes, limit=40),
        candidate_media_ids=_dedupe_uuid_strings(current.candidate_media_ids + update.candidate_media_ids, limit=160),
        rejected_scopes=_dedupe_strings(current.rejected_scopes + update.rejected_scopes, limit=40),
    )


def _tool_event_result(result: object) -> object:
    if not isinstance(result, dict):
        return result
    clipped = dict(result)
    items = clipped.get("items")
    if isinstance(items, list) and len(items) > MAX_TOOL_EVENT_ITEMS:
        clipped["items"] = items[:MAX_TOOL_EVENT_ITEMS]
        clipped["items_truncated"] = len(items) - MAX_TOOL_EVENT_ITEMS
    return clipped


def _public_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    if "final_answer" in arguments and isinstance(arguments["final_answer"], dict):
        return {"final_answer": arguments["final_answer"]}
    return {
        key: value
        for key, value in arguments.items()
        if key
        in {
            "directory_path",
            "directory_paths",
            "media_type",
            "page",
            "page_size",
            "sort",
            "query",
            "limit",
            "media_ids",
            "date_from",
            "date_to",
        }
    }


def _public_visible_response(visible_response: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(visible_response, dict):
        return {}
    return {
        "text": clip_text(clean_text(visible_response.get("text")) or clean_text(visible_response.get("answer")), 500),
        "answer_type": clean_text(visible_response.get("answer_type")),
        "confidence": clean_text(visible_response.get("confidence")),
        "checked_scope_summary": clip_text(clean_text(visible_response.get("checked_scope_summary")), 300),
        "limitations": clip_text(clean_text(visible_response.get("limitations")), 300),
    }


def _public_media_selection(media: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(media, dict):
        return {"selected_media_ids": []}
    return {"selected_media_ids": _uuid_string_list(media.get("selected_media_ids"), limit=100)}


def _page_args(arguments: dict[str, Any], *, default_page_size: int = DEFAULT_PAGE_SIZE) -> tuple[int, int]:
    page = _int_arg(arguments.get("page"), default=1, minimum=1, maximum=100000)
    page_size = _int_arg(arguments.get("page_size"), default=default_page_size, minimum=1, maximum=MAX_PAGE_SIZE)
    return page, page_size


def _page_slice(items: list[dict[str, Any]], *, page: int, page_size: int) -> tuple[list[dict[str, Any]], int]:
    total = len(items)
    offset = (page - 1) * page_size
    return items[offset : offset + page_size], total


def _int_arg(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _string_list(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return _dedupe_strings([clip_text(clean_text(item), 300) for item in value], limit=limit)


def _uuid_string_list(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return _dedupe_uuid_strings([str(item) for item in value], limit=limit)


def _dedupe_strings(values: list[str], *, limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean_text(value)
        if text and text not in seen:
            result.append(text)
            seen.add(text)
        if len(result) >= limit:
            break
    return result


def _dedupe_uuid_strings(values: list[str], *, limit: int) -> list[str]:
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
        if len(result) >= limit:
            break
    return result


def _parse_iso(value: object) -> datetime | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        return comparable_datetime(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _explicit_date_range_from_message(message: str) -> tuple[datetime | None, datetime | None]:
    season = re.search(r"(?P<year>(?:19|20)\d{2})\s*年?\s*(?:夏天|夏季)", message)
    if season:
        year = int(season.group("year"))
        return datetime(year, 6, 1), datetime(year, 8, 31, 23, 59, 59)

    year_month_range = re.search(
        r"(?P<year>(?:19|20)\d{2})\s*年?\s*(?P<start>0?[1-9]|1[0-2])\s*月?\s*(?:至|到|-|~|—)\s*(?P<end>0?[1-9]|1[0-2])\s*月?",
        message,
    )
    if year_month_range:
        year = int(year_month_range.group("year"))
        start_month = int(year_month_range.group("start"))
        end_month = int(year_month_range.group("end"))
        if start_month <= end_month:
            return datetime(year, start_month, 1), _month_end(year, end_month)

    iso_range = re.search(
        r"(?P<year>(?:19|20)\d{2})[-/](?P<start>0?[1-9]|1[0-2])\s*(?:至|到|-|~|—)\s*(?:(?:19|20)\d{2}[-/])?(?P<end>0?[1-9]|1[0-2])",
        message,
    )
    if iso_range:
        year = int(iso_range.group("year"))
        start_month = int(iso_range.group("start"))
        end_month = int(iso_range.group("end"))
        if start_month <= end_month:
            return datetime(year, start_month, 1), _month_end(year, end_month)
    return None, None


def _month_end(year: int, month: int) -> datetime:
    if month == 12:
        return datetime(year, 12, 31, 23, 59, 59)
    return datetime(year, month + 1, 1) - timedelta(seconds=1)


__all__ = [
    "FinalAnswer",
    "MediaAgentAction",
    "_list_media_descriptions",
    "run_media_library_agent_turn_events",
]
