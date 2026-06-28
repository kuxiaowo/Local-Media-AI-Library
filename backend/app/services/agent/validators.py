from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.path_utils import path_has_prefix
from app.services.agent.types import AgentPlan, JudgeResult, MediaCandidate, Scope
from app.services.agent.utils import clamp_score, clean_text, comparable_datetime


def validate_judge_result(
    result: JudgeResult,
    *,
    candidates: list[MediaCandidate],
    evidence_items: list[dict[str, Any]],
    plan: AgentPlan,
    scope: Scope,
    limit: int,
) -> JudgeResult:
    evidence_by_id = {clean_text(item.get("media_id")): item for item in evidence_items}
    candidate_by_id = {str(candidate.media_id): candidate for candidate in candidates}
    allowed_ids = set(evidence_by_id) or set(candidate_by_id)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    allow_media_output = plan.output_mode in {"media_grid", "mixed"}

    for item in result.selected:
        media_id = clean_text(item.get("media_id"))
        if not media_id or media_id in seen or media_id not in allowed_ids:
            continue
        evidence = evidence_by_id.get(media_id, {})
        candidate = candidate_by_id.get(media_id)
        if not _matches_media_type(evidence, candidate, scope.media_type):
            continue
        if not _within_directories(evidence, candidate, scope.directory_paths):
            continue
        if not _within_time(evidence, candidate, scope):
            continue
        seen.add(media_id)
        selected.append(
            {
                **item,
                "media_id": media_id,
                "score": clamp_score(item.get("score")),
                "confidence": clamp_score(item.get("confidence"), 0.5),
            }
        )
        if len(selected) >= limit:
            break

    if not allow_media_output:
        selected_for_blocks: list[dict[str, Any]] = []
    else:
        selected_for_blocks = selected

    stats = dict(result.stats or {})
    stats.setdefault("checked_count", len(evidence_items))
    stats.setdefault("matched_count", len(selected))
    return JudgeResult(
        answer_type=result.answer_type,
        text_answer=result.text_answer,
        selected=selected_for_blocks,
        rejected=result.rejected,
        summary=result.summary,
        stats=stats,
        confidence=clamp_score(result.confidence, 0.5),
    )


def _matches_media_type(evidence: dict[str, Any], candidate: MediaCandidate | None, media_type: str) -> bool:
    if media_type == "any":
        return True
    actual = clean_text(evidence.get("media_type")) or (candidate.media_type if candidate else "")
    return actual == media_type


def _within_directories(
    evidence: dict[str, Any],
    candidate: MediaCandidate | None,
    directory_paths: list[str],
) -> bool:
    if not directory_paths:
        return True
    paths = [
        clean_text(evidence.get("path")),
        clean_text(evidence.get("parent_dir")),
        clean_text(evidence.get("root_path")),
    ]
    if candidate is not None:
        paths.extend([candidate.path, candidate.parent_dir or "", candidate.root_path or ""])
    return any(path and any(path_has_prefix(path, directory) for directory in directory_paths) for path in paths)


def _within_time(evidence: dict[str, Any], candidate: MediaCandidate | None, scope: Scope) -> bool:
    if scope.date_from is None and scope.date_to is None:
        return True
    captured_at = comparable_datetime(
        _parse_evidence_datetime(evidence.get("captured_at")) or (candidate.captured_at if candidate else None)
    )
    date_from = comparable_datetime(scope.date_from)
    date_to = comparable_datetime(scope.date_to)
    if captured_at is None:
        return False
    if date_from is not None and captured_at < date_from:
        return False
    if date_to is not None and captured_at > date_to:
        return False
    return True


def _parse_evidence_datetime(value: object) -> datetime | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None

