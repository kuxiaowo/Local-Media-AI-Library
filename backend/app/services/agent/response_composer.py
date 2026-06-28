from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from app.models.schemas import ChatStreamRequest
from app.services.agent.types import AgentEvent, AgentPlan, JudgeResult, MediaCandidate, Scope
from app.services.agent.utils import clean_text, clamp_score


_MAX_DISPLAY_MEDIA = 30


def compose_response_blocks(
    *,
    request: ChatStreamRequest,
    plan: AgentPlan,
    scope: Scope,
    judge_result: JudgeResult,
    candidates: list[MediaCandidate],
) -> list[dict[str, Any]]:
    if plan.output_mode == "clarification":
        question = plan.clarification_question or "我需要更多信息才能继续。你想在哪个目录或哪些媒体里处理？"
        return [{"type": "clarification", "question": question}]

    blocks: list[dict[str, Any]] = []
    if plan.output_mode == "summary":
        text = judge_result.summary or judge_result.text_answer or "没有可总结的内容。"
        blocks.append({"type": "summary", "title": "范围总结", "text": text})
        if judge_result.stats:
            blocks.append({"type": "stats", "stats": judge_result.stats})
        return blocks

    if plan.output_mode in {"question_answer", "text_answer"}:
        answer = judge_result.text_answer or judge_result.summary or "没有足够证据回答。"
        if plan.output_mode == "question_answer":
            blocks.append(
                {
                    "type": "question_answer",
                    "question": request.message,
                    "answer": answer,
                    "confidence": judge_result.confidence,
                    "basis": judge_result.stats,
                }
            )
        else:
            blocks.append({"type": "text", "text": answer})
        if judge_result.stats:
            blocks.append({"type": "stats", "stats": judge_result.stats})
        return blocks

    if judge_result.text_answer:
        blocks.append({"type": "text", "text": judge_result.text_answer})

    media_items = _items_from_selection(
        judge_result.selected,
        candidates,
        limit=min(request.limit, _MAX_DISPLAY_MEDIA),
    )
    if media_items and plan.output_mode in {"media_grid", "mixed"}:
        blocks.append({"type": "media_grid", "title": _media_title(plan), "items": media_items})

    if judge_result.stats and plan.output_mode == "mixed":
        blocks.append({"type": "stats", "stats": judge_result.stats})

    if not blocks:
        blocks.append({"type": "text", "text": judge_result.text_answer or "没有找到符合条件的媒体。"})
    return blocks


def compose_explain_blocks(message: str) -> list[dict[str, Any]]:
    text = (
        "可能原因通常有几类：媒体还没有完成 AI 分析；摘要里没有包含你使用的关键词；"
        "embedding 模型对这个语义不敏感；当前目录、媒体类型或时间过滤过窄；"
        "或者目标媒体不在已启用的媒体库根目录中。可以先放宽过滤范围，再换更具体的视觉描述重试。"
    )
    if "为什么" not in message and "搜不到" not in message:
        text = "这个 Agent 会先理解当前页面、目录、选择和历史结果，再决定是在范围内总结、问答、筛选还是召回候选媒体。"
    return [{"type": "text", "text": text}]


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
        block_type = clean_text(raw_block.get("type"))
        if block_type in {"text", "summary", "question_answer", "clarification", "stats", "comparison"}:
            block = {key: value for key, value in raw_block.items() if key != "items"}
            if block_type == "text" and not clean_text(block.get("text")):
                continue
            blocks.append(block)
        elif block_type == "media_grid":
            items: list[dict[str, Any]] = []
            for raw_item in raw_block.get("items") or []:
                if remaining_media <= 0:
                    break
                if not isinstance(raw_item, dict):
                    continue
                media_id = clean_text(raw_item.get("media_id"))
                if not media_id or media_id in seen_media or media_id not in by_id:
                    continue
                seen_media.add(media_id)
                remaining_media -= 1
                items.append(
                    by_id[media_id].to_item(
                        reason=clean_text(raw_item.get("reason")) or None,
                        score=clamp_score(raw_item.get("score"), by_id[media_id].score),
                    )
                )
            if items:
                title = clean_text(raw_block.get("title"))
                blocks.append({"type": "media_grid", "title": title or None, "items": items})

    if not blocks:
        answer = clean_text(raw.get("answer")) or "已完成。"
        blocks.append({"type": "text", "text": answer})
    return blocks


async def stream_blocks(blocks: list[dict[str, Any]]) -> AsyncIterator[AgentEvent]:
    for index, block in enumerate(blocks):
        block_id = f"block-{index}"
        block_type = block.get("type")
        if block_type == "text":
            yield AgentEvent("text_start", {"block_id": block_id})
            for char in str(block.get("text") or ""):
                yield AgentEvent("text_delta", {"block_id": block_id, "text": char})
                await asyncio.sleep(0)
            yield AgentEvent("text_end", {"block_id": block_id})
        elif block_type == "media_grid":
            yield AgentEvent("media_block", {"block_id": block_id, **block})
        elif block_type == "summary":
            yield AgentEvent("summary_block", {"block_id": block_id, **block})
        elif block_type == "question_answer":
            yield AgentEvent("qa_block", {"block_id": block_id, **block})
        elif block_type == "clarification":
            yield AgentEvent("clarification_block", {"block_id": block_id, **block})
        elif block_type == "stats":
            yield AgentEvent("stats_block", {"block_id": block_id, **block})
        else:
            yield AgentEvent("text_start", {"block_id": block_id})
            text = str(block.get("text") or block.get("answer") or block.get("summary") or "")
            for char in text:
                yield AgentEvent("text_delta", {"block_id": block_id, "text": char})
                await asyncio.sleep(0)
            yield AgentEvent("text_end", {"block_id": block_id})


def blocks_to_text(blocks: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for block in blocks:
        block_type = block.get("type")
        if block_type == "text":
            parts.append(clean_text(block.get("text")))
        elif block_type == "summary":
            parts.append(clean_text(block.get("text") or block.get("summary")))
        elif block_type == "question_answer":
            parts.append(clean_text(block.get("answer")))
        elif block_type == "clarification":
            parts.append(clean_text(block.get("question")))
    return "\n\n".join(part for part in parts if part)


def _items_from_selection(
    selected: list[dict[str, Any]],
    candidates: list[MediaCandidate],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    by_id = {str(candidate.media_id): candidate for candidate in candidates}
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in selected:
        media_id = clean_text(item.get("media_id"))
        if not media_id or media_id in seen or media_id not in by_id:
            continue
        seen.add(media_id)
        candidate = by_id[media_id]
        items.append(
            candidate.to_item(
                reason=clean_text(item.get("reason")) or candidate.reason,
                score=clamp_score(item.get("score"), candidate.score),
            )
        )
        if len(items) >= limit:
            break
    return items


def _media_title(plan: AgentPlan) -> str:
    if plan.task_type == "recommend":
        return "推荐媒体"
    if plan.task_type == "refine":
        return "继续筛选结果"
    return "匹配媒体"

