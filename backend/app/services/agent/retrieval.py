from __future__ import annotations

import uuid

from sqlalchemy import literal, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.db_models import EmbeddingProfile, MediaAiSummary, MediaEmbedding, MediaFile
from app.models.schemas import ChatStreamRequest
from app.services.agent.types import AgentPlan, MediaCandidate, QueryExpansion, Scope
from app.services.agent.utils import directory_filter
from app.services.media_visibility import visible_media_filter
from app.services.ollama_client import OllamaClient
from app.services.search_rerank import keyword_score
from app.services.vector_math import cosine_similarity


async def retrieve_broad_candidates(
    db: Session,
    ollama: OllamaClient,
    request: ChatStreamRequest,
    plan: AgentPlan,
    scope: Scope,
    expansion: QueryExpansion,
) -> list[MediaCandidate]:
    if plan.task_type == "summarize":
        return []

    target_limit = _retrieval_limit(plan, request)
    scoped_rows = _load_scoped_rows(db, scope)
    if not scoped_rows:
        return []

    query_texts = expansion.positive_queries or [plan.semantic_query or request.message]
    query_vectors = await _query_vectors(db, ollama, query_texts)
    candidates: list[MediaCandidate] = []
    scoped_ids = set(scope.media_ids)

    for media, summary, embedding in scoped_rows:
        vector_score = _best_vector_score(query_vectors, embedding)
        keyword = max(keyword_score(query, summary.searchable_text or "") for query in query_texts)
        metadata_score = 1.0 if plan.media_type == "any" or media.media_type == plan.media_type else 0.0
        directory_score = 1.0 if scope.directory_paths or media.id in scoped_ids else 0.3
        time_score = _time_score(media, scope)
        history_score = 1.0 if media.id in scoped_ids else 0.0
        score = (
            0.42 * vector_score
            + 0.28 * keyword
            + 0.12 * metadata_score
            + 0.08 * time_score
            + 0.06 * directory_score
            + 0.04 * history_score
        )
        candidates.append(
            _candidate_from_row(
                media,
                summary,
                score=score,
                reason=(
                    f"粗召回：vector={vector_score:.3f}, keyword={keyword:.3f}, "
                    f"metadata={metadata_score:.3f}, time={time_score:.3f}, directory={directory_score:.3f}"
                ),
            )
        )

    candidates.sort(key=lambda item: item.score, reverse=True)
    if plan.task_type == "refine" and scope.media_ids:
        scoped = [candidate for candidate in candidates if candidate.media_id in scoped_ids]
        if len(scoped) >= min(request.limit, 5):
            return scoped[:target_limit]
    return candidates[:target_limit]


def load_scope_media_for_summary(db: Session, scope: Scope, *, limit: int | None = None) -> list[MediaCandidate]:
    rows = _load_scoped_rows(db, scope, include_embedding=False)
    candidates = [_candidate_from_row(media, summary, score=0.0, reason="范围内已分析媒体") for media, summary, _embedding in rows]
    candidates.sort(key=lambda item: (item.captured_at is None, item.captured_at), reverse=True)
    return candidates[:limit] if limit else candidates


def _load_scoped_rows(
    db: Session,
    scope: Scope,
    *,
    include_embedding: bool = True,
) -> list[tuple[MediaFile, MediaAiSummary, list[float] | None]]:
    if include_embedding:
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
            stmt = select(MediaFile, MediaAiSummary, literal(None)).join(MediaAiSummary, MediaAiSummary.media_id == MediaFile.id)
    else:
        stmt = select(MediaFile, MediaAiSummary, literal(None)).join(MediaAiSummary, MediaAiSummary.media_id == MediaFile.id)

    stmt = stmt.where(MediaFile.status == scope.analysis_status, visible_media_filter(db))
    if scope.media_type != "any":
        stmt = stmt.where(MediaFile.media_type == scope.media_type)
    if scope.media_ids:
        stmt = stmt.where(MediaFile.id.in_(scope.media_ids))
    if scope.directory_paths:
        stmt = stmt.where(or_(*(directory_filter(path) for path in scope.directory_paths)))
    if scope.date_from is not None:
        stmt = stmt.where(MediaFile.captured_at >= scope.date_from)
    if scope.date_to is not None:
        stmt = stmt.where(MediaFile.captured_at <= scope.date_to)
    return list(db.execute(stmt).all())


def _candidate_from_row(
    media: MediaFile,
    summary: MediaAiSummary,
    *,
    score: float,
    reason: str,
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
        score=max(0.0, min(1.0, score)),
        reason=reason,
        root_path=media.root_path,
    )


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


def _retrieval_limit(plan: AgentPlan, request: ChatStreamRequest) -> int:
    defaults = {
        "find": 200,
        "recommend": 300,
        "question_answer": 200,
        "filter": 200,
        "refine": 200,
        "compare": 100,
    }
    return max(request.limit, min(400, max(request.candidate_k, defaults.get(plan.task_type, 200))))


async def _query_vectors(db: Session, ollama: OllamaClient, queries: list[str]) -> list[list[float]]:
    model_name = get_settings().default_embedding_model.strip()
    if not model_name or _embedding_profile_id(db) is None:
        return []
    vectors: list[list[float]] = []
    for query in queries[:8]:
        try:
            vectors.append(await ollama.embed_text(model=model_name, text=query))
        except Exception:
            return []
    return vectors


def _embedding_profile_id(db: Session):
    model_name = get_settings().default_embedding_model.strip()
    if not model_name:
        return None
    return db.scalar(select(EmbeddingProfile.id).where(EmbeddingProfile.model_name == model_name))


def _best_vector_score(query_vectors: list[list[float]], embedding: list[float] | None) -> float:
    if not query_vectors or not embedding:
        return 0.0
    return max(max(0.0, min(1.0, cosine_similarity(vector, embedding))) for vector in query_vectors)


def _time_score(media: MediaFile, scope: Scope) -> float:
    if scope.date_from is None and scope.date_to is None:
        return 0.5
    if media.captured_at is None:
        return 0.0
    if scope.date_from is not None and media.captured_at < scope.date_from:
        return 0.0
    if scope.date_to is not None and media.captured_at > scope.date_to:
        return 0.0
    return 1.0
