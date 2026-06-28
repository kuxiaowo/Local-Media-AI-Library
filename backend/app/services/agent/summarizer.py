from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.schemas import ChatStreamRequest
from app.services.agent.evidence_builder import build_evidence
from app.services.agent.retrieval import load_scope_media_for_summary
from app.services.agent.types import AgentPlan, JudgeResult, Scope
from app.services.agent.utils import clean_text, clip_text
from app.services.ollama_client import OllamaClient


SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "themes": {"type": "array", "items": {"type": "string"}},
        "common_scenes": {"type": "array", "items": {"type": "string"}},
        "time_span": {"type": ["string", "null"]},
        "notable_items": {"type": "array", "items": {"type": "string"}},
        "representative_media_ids": {"type": "array", "items": {"type": "string"}},
        "stats": {"type": "object"},
    },
    "required": ["summary", "themes", "common_scenes", "time_span", "notable_items", "representative_media_ids", "stats"],
}

SUMMARY_SYSTEM_PROMPT = """你是本地媒体库 Summarizer。
你只能根据 evidence 里的摘要文本总结，不要假设看过原图或视频。
总结要覆盖主要主题、常见场景、人物/物体/地点线索、时间跨度、异常或值得注意内容。
只返回符合 schema 的 JSON。"""


async def summarize_scope(
    *,
    db: Session,
    ollama: OllamaClient,
    model_name: str,
    request: ChatStreamRequest,
    plan: AgentPlan,
    scope: Scope,
) -> JudgeResult:
    candidates = load_scope_media_for_summary(db, scope)
    if not candidates:
        return JudgeResult(
            answer_type="summary",
            text_answer="当前范围内没有已完成分析的媒体。",
            summary="当前范围内没有已完成分析的媒体。",
            stats={"checked_count": 0, "matched_count": 0},
            confidence=0.7,
        )

    partials: list[dict[str, Any]] = []
    for chunk in _chunks(candidates, max_items=75):
        evidence = build_evidence(db, [candidate.media_id for candidate in chunk], candidates=chunk, detail_level="light")
        partials.append(await _summarize_evidence(ollama, model_name, request, plan, scope, evidence))

    if len(partials) == 1:
        raw = partials[0]
    else:
        raw = await _merge_partials(ollama, model_name, request, plan, scope, partials)

    summary = clean_text(raw.get("summary")) or _fallback_summary(candidates)
    stats = dict(raw.get("stats") or {})
    stats.setdefault("checked_count", len(candidates))
    stats.setdefault("image_count", sum(1 for candidate in candidates if candidate.media_type == "image"))
    stats.setdefault("video_count", sum(1 for candidate in candidates if candidate.media_type == "video"))
    dates = [candidate.captured_at for candidate in candidates if candidate.captured_at is not None]
    if dates:
        stats.setdefault("date_min", min(dates).isoformat())
        stats.setdefault("date_max", max(dates).isoformat())
    selected = [
        {"media_id": media_id, "score": 0.7, "reason": "总结中的代表媒体", "matched_requirements": [], "failed_requirements": [], "confidence": 0.6}
        for media_id in _representative_ids(raw, candidates)[: min(9, request.limit)]
    ]
    return JudgeResult(
        answer_type="summary",
        text_answer=summary,
        selected=selected if plan.output_mode == "mixed" else [],
        rejected=[],
        summary=summary,
        stats=stats,
        confidence=0.65,
    )


async def _summarize_evidence(
    ollama: OllamaClient,
    model_name: str,
    request: ChatStreamRequest,
    plan: AgentPlan,
    scope: Scope,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    prompt = f"""用户请求：
{request.message}

Plan：
{json.dumps(plan.to_payload(), ensure_ascii=False, default=str)}

Scope：
{json.dumps(scope.to_payload(), ensure_ascii=False, default=str)}

Light evidence：
{json.dumps(evidence, ensure_ascii=False, default=str)}
"""
    try:
        return await ollama.generate_text_json(
            model=model_name,
            prompt=prompt,
            schema=SUMMARY_SCHEMA,
            system_prompt=SUMMARY_SYSTEM_PROMPT,
        )
    except Exception:
        return _fallback_raw_summary(evidence)


async def _merge_partials(
    ollama: OllamaClient,
    model_name: str,
    request: ChatStreamRequest,
    plan: AgentPlan,
    scope: Scope,
    partials: list[dict[str, Any]],
) -> dict[str, Any]:
    prompt = f"""用户请求：
{request.message}

请合并这些分批总结，输出一个最终 summary JSON。

Plan：
{json.dumps(plan.to_payload(), ensure_ascii=False, default=str)}

Scope：
{json.dumps(scope.to_payload(), ensure_ascii=False, default=str)}

Partials：
{json.dumps(partials, ensure_ascii=False, default=str)}
"""
    try:
        return await ollama.generate_text_json(
            model=model_name,
            prompt=prompt,
            schema=SUMMARY_SCHEMA,
            system_prompt=SUMMARY_SYSTEM_PROMPT,
        )
    except Exception:
        text = "\n".join(clean_text(item.get("summary")) for item in partials if clean_text(item.get("summary")))
        stats = {"checked_count": sum(int((item.get("stats") or {}).get("checked_count", 0)) for item in partials)}
        return {"summary": clip_text(text, 2000), "representative_media_ids": [], "stats": stats}


def _fallback_raw_summary(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    lines = []
    for item in evidence[:20]:
        summary = clean_text(item.get("short_summary") or item.get("searchable_text"))
        if summary:
            lines.append(f"- {summary}")
    return {
        "summary": "这个范围内的媒体主要包括：\n" + "\n".join(lines) if lines else "没有足够摘要可总结。",
        "themes": [],
        "common_scenes": [],
        "time_span": None,
        "notable_items": [],
        "representative_media_ids": [item.get("media_id") for item in evidence[:6] if item.get("media_id")],
        "stats": {"checked_count": len(evidence)},
    }


def _fallback_summary(candidates) -> str:
    examples = [candidate.short_summary for candidate in candidates[:10] if candidate.short_summary]
    if not examples:
        return f"当前范围内有 {len(candidates)} 个已分析媒体，但摘要信息较少。"
    return "当前范围内的媒体大致包括：\n" + "\n".join(f"- {item}" for item in examples)


def _representative_ids(raw: dict[str, Any], candidates) -> list[str]:
    ids = [clean_text(value) for value in raw.get("representative_media_ids") or [] if clean_text(value)]
    valid = {str(candidate.media_id) for candidate in candidates}
    result = [media_id for media_id in ids if media_id in valid]
    if result:
        return result
    return [str(candidate.media_id) for candidate in candidates[:6]]


def _chunks(candidates, *, max_items: int):
    for index in range(0, len(candidates), max_items):
        yield candidates[index : index + max_items]

