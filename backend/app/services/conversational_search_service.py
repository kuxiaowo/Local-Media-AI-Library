from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.db_models import SearchMessage
from app.models.schemas import ChatStreamRequest
from app.services.agent.context_builder import build_context_pack
from app.services.agent.evidence_builder import build_evidence
from app.services.agent.judge import judge_candidates
from app.services.agent.planner import plan_request
from app.services.agent.query_expander import expand_queries
from app.services.agent.response_composer import (
    _validated_blocks,
    blocks_to_text,
    compose_explain_blocks,
    compose_response_blocks,
    stream_blocks,
)
from app.services.agent.retrieval import _merge_candidates, retrieve_broad_candidates
from app.services.agent.scope_resolver import resolve_scope
from app.services.agent.summarizer import summarize_scope
from app.services.agent.types import AgentEvent, JudgeResult, MediaCandidate
from app.services.agent.validators import validate_judge_result
from app.services.ollama_client import OllamaClient


async def run_agent_turn_events(
    db: Session,
    request: ChatStreamRequest,
    history: list[SearchMessage],
    ollama: OllamaClient,
) -> AsyncIterator[AgentEvent]:
    settings = get_settings()
    model_name = settings.default_ai_search_model.strip() or settings.default_summary_model
    context_pack = build_context_pack(db, request, history)
    plan = await plan_request(ollama=ollama, model_name=model_name, request=request, context_pack=context_pack)
    yield AgentEvent("plan", {"plan": plan.to_payload()})

    if plan.output_mode == "clarification":
        blocks = compose_response_blocks(
            request=request,
            plan=plan,
            scope=resolve_scope(plan, request, context_pack),
            judge_result=JudgeResult(text_answer=plan.clarification_question or ""),
            candidates=[],
        )
        async for event in stream_blocks(blocks):
            yield event
        yield _assistant_message(blocks, model_name, candidate_count=0)
        return

    scope = resolve_scope(plan, request, context_pack)
    yield AgentEvent("scope", {"scope": scope.to_payload()})

    if not plan.needs_media_evidence and plan.task_type == "explain":
        blocks = compose_explain_blocks(request.message)
        async for event in stream_blocks(blocks):
            yield event
        yield _assistant_message(blocks, model_name, candidate_count=0)
        return

    if plan.task_type == "summarize":
        yield AgentEvent("judge_progress", {"stage": "summarize_scope"})
        summary_result = await summarize_scope(
            db=db,
            ollama=ollama,
            model_name=model_name,
            request=request,
            plan=plan,
            scope=scope,
        )
        blocks = compose_response_blocks(
            request=request,
            plan=plan,
            scope=scope,
            judge_result=summary_result,
            candidates=[],
        )
        async for event in stream_blocks(blocks):
            yield event
        yield _assistant_message(blocks, model_name, candidate_count=summary_result.stats.get("checked_count", 0))
        return

    expansion = await expand_queries(ollama=ollama, model_name=model_name, request=request, plan=plan)
    yield AgentEvent("retrieval_progress", {"stage": "query_expanded", **expansion.to_payload()})
    candidates = await retrieve_broad_candidates(db, ollama, request, plan, scope, expansion)
    yield AgentEvent(
        "retrieval_progress",
        {
            "stage": "broad_retrieval_done",
            "candidate_count": len(candidates),
        },
    )

    detail_level = "video_timeline" if plan.media_type == "video" or _has_video_candidate(candidates) else "detailed"
    evidence = build_evidence(
        db,
        [candidate.media_id for candidate in candidates],
        candidates=candidates,
        detail_level=detail_level,
    )
    yield AgentEvent("evidence_loaded", {"count": len(evidence), "detail_level": detail_level})

    raw_judge = await judge_candidates(
        ollama=ollama,
        model_name=model_name,
        request=request,
        plan=plan,
        scope=scope,
        evidence_items=evidence,
    )
    yield AgentEvent("judge_progress", {"stage": "judge_done", "confidence": raw_judge.confidence})
    validated = validate_judge_result(
        raw_judge,
        candidates=candidates,
        evidence_items=evidence,
        plan=plan,
        scope=scope,
        limit=request.limit,
    )
    blocks = compose_response_blocks(
        request=request,
        plan=plan,
        scope=scope,
        judge_result=validated,
        candidates=candidates,
    )
    async for event in stream_blocks(blocks):
        yield event
    yield _assistant_message(blocks, model_name, candidate_count=len(candidates))


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


def _has_video_candidate(candidates: list[MediaCandidate]) -> bool:
    return any(candidate.media_type == "video" for candidate in candidates)


__all__ = [
    "AgentEvent",
    "MediaCandidate",
    "_merge_candidates",
    "_validated_blocks",
    "run_agent_turn_events",
]
