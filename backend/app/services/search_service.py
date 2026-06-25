from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.db_models import DirectoryRule, EmbeddingProfile, MediaAiSummary, MediaEmbedding, MediaFile
from app.models.schemas import ParsedFilters, SearchRequest, SearchResponse, SearchResultItem
from app.services.media_visibility import visible_media_filter
from app.services.ollama_client import OllamaClient
from app.services.search_rerank import RerankInput, final_score
from app.services.vector_math import cosine_similarity


def parse_query_filters(request: SearchRequest) -> ParsedFilters:
    semantic_query = request.query
    media_type = request.media_type
    if media_type == "any":
        if re.search(r"视频|录像|片段|影片|短片", request.query):
            media_type = "video"
        elif re.search(r"照片|图片|图像|相片|影像", request.query):
            media_type = "image"

    return ParsedFilters(
        media_type=media_type,
        date_from=request.date_from,
        date_to=request.date_to,
        semantic_query=semantic_query,
    )


async def search_media(db: Session, request: SearchRequest, ollama: OllamaClient) -> SearchResponse:
    parsed = parse_query_filters(request)
    model_name = _select_embedding_model(db)
    if model_name is None:
        return SearchResponse(query=request.query, parsed_filters=parsed, results=[])

    query_vector = await ollama.embed_text(model=model_name, text=parsed.semantic_query)
    profile = db.scalar(select(EmbeddingProfile).where(EmbeddingProfile.model_name == model_name))
    if profile is None:
        return SearchResponse(query=request.query, parsed_filters=parsed, results=[])

    stmt = (
        select(MediaFile, MediaAiSummary, MediaEmbedding.embedding)
        .join(MediaAiSummary, MediaAiSummary.media_id == MediaFile.id)
        .join(MediaEmbedding, MediaEmbedding.media_id == MediaFile.id)
        .where(MediaEmbedding.profile_id == profile.id, MediaFile.status == "done", visible_media_filter(db))
    )
    if parsed.media_type != "any":
        stmt = stmt.where(MediaFile.media_type == parsed.media_type)
    if parsed.date_from is not None:
        stmt = stmt.where(MediaFile.captured_at >= parsed.date_from)
    if parsed.date_to is not None:
        stmt = stmt.where(MediaFile.captured_at <= parsed.date_to)
    if request.directory_rule_ids:
        roots = db.scalars(
            select(DirectoryRule.normalized_path).where(DirectoryRule.id.in_(request.directory_rule_ids))
        ).all()
        if roots:
            stmt = stmt.where(MediaFile.root_path.in_(roots))

    rows = db.execute(stmt).all()
    results: list[tuple[float, SearchResultItem]] = []
    selected_roots = set()
    if request.directory_rule_ids:
        selected_roots = set(
            db.scalars(
                select(DirectoryRule.normalized_path).where(DirectoryRule.id.in_(request.directory_rule_ids))
            ).all()
        )

    for media, summary, embedding in rows:
        vector_score = max(0.0, min(1.0, cosine_similarity(query_vector, embedding)))
        text = summary.searchable_text or ""
        score = final_score(
            RerankInput(
                vector_score=vector_score,
                query=request.query,
                text=text,
                captured_at=media.captured_at,
                date_from=parsed.date_from,
                date_to=parsed.date_to,
                folder_match=bool(selected_roots and media.root_path in selected_roots),
            )
        )
        reason = _match_reason(request.query, summary)
        results.append(
            (
                score,
                SearchResultItem(
                    media_id=media.id,
                    path=media.path,
                    thumbnail_url=f"/api/media/{media.id}/thumbnail",
                    media_type=media.media_type,
                    captured_at=media.captured_at,
                    title=summary.title,
                    short_summary=summary.short_summary,
                    match_reason=reason,
                    score=score,
                ),
            )
        )
    results.sort(key=lambda item: item[0], reverse=True)
    return SearchResponse(
        query=request.query,
        parsed_filters=parsed,
        results=[item for _, item in results[: request.limit]],
    )


def _select_embedding_model(db: Session) -> str | None:
    settings_model = get_settings().default_embedding_model.strip()
    if settings_model:
        return settings_model
    profile_model = db.scalar(select(EmbeddingProfile.model_name).limit(1))
    if profile_model:
        return profile_model
    return None


def _match_reason(query: str, summary: MediaAiSummary) -> str:
    keywords = summary.search_keywords or []
    if isinstance(keywords, list) and keywords:
        return f"匹配到摘要关键词：{', '.join(str(item) for item in keywords[:5])}"
    if summary.short_summary:
        return f"语义上接近：{summary.short_summary[:80]}"
    return f"与查询“{query}”的向量语义相近"
