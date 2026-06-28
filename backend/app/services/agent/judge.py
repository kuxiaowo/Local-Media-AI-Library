from __future__ import annotations

import json
from typing import Any

from app.models.schemas import ChatStreamRequest
from app.services.agent.types import AgentPlan, JudgeResult, Scope
from app.services.agent.utils import clamp_score, clean_text, clip_text
from app.services.ollama_client import OllamaClient


JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer_type": {"type": "string"},
        "text_answer": {"type": "string"},
        "selected": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "media_id": {"type": "string"},
                    "score": {"type": "number"},
                    "reason": {"type": "string"},
                    "matched_requirements": {"type": "array", "items": {"type": "string"}},
                    "failed_requirements": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number"},
                },
                "required": ["media_id", "score", "reason", "matched_requirements", "failed_requirements", "confidence"],
            },
        },
        "rejected": {
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
        "summary": {"type": "string"},
        "stats": {"type": "object"},
        "confidence": {"type": "number"},
    },
    "required": ["answer_type", "text_answer", "selected", "rejected", "summary", "stats", "confidence"],
}

JUDGE_SYSTEM_PROMPT = """你是本地媒体库 AI Judge。
你只能根据 evidence_items 判断，不能假设自己看过原图、视频或音频。
如果 plan.needs_visual_reinspection=true，但 evidence 没有 visual_reinspection 字段，必须说明判断只基于已有摘要。
所有 media_id 必须来自 evidence_items。
评分规则：0.90-1.00 高度符合；0.70-0.89 基本符合；0.40-0.69 部分相关但不理想；0.00-0.39 不符合或证据不足。
证据不足时不要硬猜，要在 reason 或 text_answer 中说明 uncertain。
只返回符合 schema 的 JSON。"""


async def judge_candidates(
    *,
    ollama: OllamaClient,
    model_name: str,
    request: ChatStreamRequest,
    plan: AgentPlan,
    scope: Scope,
    evidence_items: list[dict[str, Any]],
) -> JudgeResult:
    if not evidence_items:
        return JudgeResult(
            answer_type=plan.output_mode,
            text_answer="没有找到可用于判断的已分析媒体。",
            selected=[],
            rejected=[],
            summary="没有可用证据。",
            stats={"checked_count": 0, "matched_count": 0},
            confidence=0.4,
        )

    prompt = f"""用户请求：
{request.message}

Plan：
{json.dumps(plan.to_payload(), ensure_ascii=False, default=str)}

Scope：
{json.dumps(scope.to_payload(), ensure_ascii=False, default=str)}

Evidence items：
{json.dumps(evidence_items, ensure_ascii=False, default=str)}

请基于 evidence 输出最终判断。"""
    try:
        raw = await ollama.generate_text_json(
            model=model_name,
            prompt=prompt,
            schema=JUDGE_SCHEMA,
            system_prompt=JUDGE_SYSTEM_PROMPT,
        )
        return normalize_judge_result(raw)
    except Exception:
        return fallback_judge_result(request, plan, evidence_items)


def normalize_judge_result(raw: dict[str, Any]) -> JudgeResult:
    selected = []
    for item in raw.get("selected") or []:
        if not isinstance(item, dict):
            continue
        selected.append(
            {
                "media_id": clean_text(item.get("media_id")),
                "score": clamp_score(item.get("score")),
                "reason": clean_text(item.get("reason")) or "AI 判断相关",
                "matched_requirements": _string_list(item.get("matched_requirements")),
                "failed_requirements": _string_list(item.get("failed_requirements")),
                "confidence": clamp_score(item.get("confidence"), 0.5),
            }
        )
    rejected = []
    for item in raw.get("rejected") or []:
        if isinstance(item, dict):
            rejected.append(
                {
                    "media_id": clean_text(item.get("media_id")),
                    "score": clamp_score(item.get("score")),
                    "reason": clean_text(item.get("reason")) or "不符合要求",
                }
            )
    return JudgeResult(
        answer_type=clean_text(raw.get("answer_type")) or "media_selection",
        text_answer=clean_text(raw.get("text_answer")),
        selected=selected,
        rejected=rejected,
        summary=clean_text(raw.get("summary")),
        stats=dict(raw.get("stats") or {}),
        confidence=clamp_score(raw.get("confidence"), 0.5),
    )


def fallback_judge_result(
    request: ChatStreamRequest,
    plan: AgentPlan,
    evidence_items: list[dict[str, Any]],
) -> JudgeResult:
    ordered = sorted(evidence_items, key=lambda item: float(item.get("initial_score") or 0.0), reverse=True)
    if plan.output_mode in {"question_answer", "text_answer"} or plan.task_type == "question_answer":
        checked_count = len(evidence_items)
        matched = [item for item in ordered if _rough_match(item, plan)]
        answer = _qa_answer(request.message, checked_count, len(matched), plan)
        return JudgeResult(
            answer_type="question_answer",
            text_answer=answer,
            selected=[
                _selected_payload(item, score=max(float(item.get("initial_score") or 0.0), 0.4), reason="可作为代表证据")
                for item in matched[: min(5, request.limit)]
            ],
            rejected=[],
            summary=answer,
            stats={
                "checked_count": checked_count,
                "matched_count": len(matched),
                "representative_media_ids": [item.get("media_id") for item in matched[:5]],
            },
            confidence=0.55,
        )

    selected = [
        _selected_payload(item, score=float(item.get("initial_score") or 0.0), reason=item.get("initial_reason") or "粗召回候选")
        for item in ordered[: request.limit]
    ]
    note = ""
    if plan.needs_visual_reinspection:
        note = " 注意：该请求可能需要重新视觉检查，当前只基于已有摘要判断。"
    return JudgeResult(
        answer_type="media_selection",
        text_answer=f"我根据已有摘要先筛出这些候选。{note}".strip(),
        selected=selected,
        rejected=[],
        summary=f"候选数 {len(evidence_items)}，返回前 {len(selected)} 个。",
        stats={"checked_count": len(evidence_items), "matched_count": len(selected)},
        confidence=0.5,
    )


def _rough_match(item: dict[str, Any], plan: AgentPlan) -> bool:
    haystack = " ".join(
        clean_text(item.get(key))
        for key in ["title", "short_summary", "detailed_summary", "scene", "searchable_text"]
    ).lower()
    requirements = [requirement.lower() for requirement in plan.positive_requirements if requirement]
    if not requirements:
        return float(item.get("initial_score") or 0.0) > 0.1
    return any(requirement in haystack for requirement in requirements) or float(item.get("initial_score") or 0.0) > 0.35


def _qa_answer(message: str, checked_count: int, matched_count: int, plan: AgentPlan) -> str:
    if checked_count == 0:
        return "没有可检查的已分析媒体。"
    ratio = matched_count / checked_count
    if "大部分" in message or "多数" in message:
        if ratio >= 0.6:
            return f"根据已有摘要看，大部分符合这个描述：检查 {checked_count} 个，约 {matched_count} 个匹配。"
        return f"根据已有摘要看，不能说大部分符合：检查 {checked_count} 个，约 {matched_count} 个匹配。"
    if matched_count:
        return f"根据已有摘要，找到了相关证据：检查 {checked_count} 个，约 {matched_count} 个匹配。"
    return f"根据已有摘要，没有找到明确匹配证据；检查了 {checked_count} 个媒体。"


def _selected_payload(item: dict[str, Any], *, score: float, reason: object) -> dict[str, Any]:
    return {
        "media_id": clean_text(item.get("media_id")),
        "score": clamp_score(score),
        "reason": clip_text(clean_text(reason), 240),
        "matched_requirements": [],
        "failed_requirements": [],
        "confidence": 0.5,
    }


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for text in (clean_text(item) for item in value) if text]

