from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.path_utils import normalize_path
from app.models.db_models import DirectoryRule, EmbeddingProfile, MediaAiSummary, MediaEmbedding, MediaFile
from app.models.schemas import ParsedFilters, SearchRequest, SearchResponse, SearchResultItem
from app.services.media_visibility import visible_media_filter
from app.services.ollama_client import OllamaClient
from app.services.search_rerank import keyword_score
from app.services.vector_math import cosine_similarity


AI_SEARCH_SYSTEM_PROMPT = (
    "你是本地媒体库检索助手。所有结论必须只基于用户提供的媒体描述文本，"
    "不能假设你看过原图、视频或音频。只返回符合 schema 的 JSON。"
)

AI_SEARCH_INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "enum": ["find", "recommend", "summarize"],
        },
        "media_type": {
            "type": "string",
            "enum": ["image", "video", "any"],
        },
        "semantic_query": {"type": "string"},
        "date_from": {"type": ["string", "null"]},
        "date_to": {"type": ["string", "null"]},
        "directory_query": {"type": ["string", "null"]},
    },
    "required": ["task", "media_type", "semantic_query", "date_from", "date_to", "directory_query"],
}

AI_SEARCH_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "results": {
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
    "required": ["answer", "results"],
}

_VIDEO_RE = re.compile(r"视频|录像|片段|影片|短片")
_IMAGE_RE = re.compile(r"照片|图片|图像|相片|影像")
_AI_SEARCH_CHUNK_MAX_CHARS = 24000
_DIRECTORY_HINT_LIMIT = 80


@dataclass(frozen=True)
class AiIntent:
    task: str
    media_type: str
    semantic_query: str
    date_from: datetime | None = None
    date_to: datetime | None = None
    directory_query: str | None = None


@dataclass(frozen=True)
class AiCandidate:
    media: MediaFile
    summary: MediaAiSummary
    vector_score: float = 0.0
    keyword_score: float = 0.0

    @property
    def rank_score(self) -> float:
        return 0.75 * self.vector_score + 0.25 * self.keyword_score


async def search_media_with_ai(db: Session, request: SearchRequest, ollama: OllamaClient) -> SearchResponse:
    settings = get_settings()
    model_name = settings.default_ai_search_model.strip() or settings.default_summary_model
    directory_hints = _directory_hints(db)
    intent = await _parse_ai_intent(
        ollama=ollama,
        model_name=model_name,
        request=request,
        directory_hints=directory_hints,
    )
    parsed = _parsed_filters_from_intent(request, intent)
    rows = _load_ai_scope(db, request, parsed, intent, directory_hints)
    candidates = [AiCandidate(media=media, summary=summary) for media, summary in rows]

    if not candidates:
        return SearchResponse(
            query=request.query,
            mode="ai",
            parsed_filters=parsed,
            results=[],
            answer="没有找到符合当前范围的已分析媒体。",
            ai_model=model_name,
            scope_total=0,
        )

    scoped_total = len(candidates)
    candidates_for_model = candidates
    if intent.task != "summarize":
        candidates_for_model = await _rank_candidates(db, ollama, request, parsed.semantic_query, candidates)
        candidates_for_model = candidates_for_model[: request.candidate_k]

    raw = await _ask_ai_over_candidates(
        ollama=ollama,
        model_name=model_name,
        request=request,
        intent=intent,
        parsed=parsed,
        candidates=candidates_for_model,
    )
    results = _validated_result_items(raw, candidates_for_model, request.limit)
    return SearchResponse(
        query=request.query,
        mode="ai",
        parsed_filters=parsed,
        results=results,
        answer=_clean_text(raw.get("answer")) or "AI 检索已完成。",
        ai_model=model_name,
        scope_total=scoped_total,
    )


async def _parse_ai_intent(
    *,
    ollama: OllamaClient,
    model_name: str,
    request: SearchRequest,
    directory_hints: list[dict[str, str]],
) -> AiIntent:
    hints_text = "\n".join(
        f"- {item['name']}: {item['path']}" for item in directory_hints[:_DIRECTORY_HINT_LIMIT]
    )
    prompt = f"""今天日期：{date.today().isoformat()}

用户检索要求：
{request.query}

可选媒体目录提示：
{hints_text or "无"}

请把用户要求解析成 JSON：
- task: find 表示找特定照片/视频；recommend 表示推荐；summarize 表示总结文件夹或时间段。
- media_type: image, video, any。
- semantic_query: 保留用于语义匹配的核心检索文本。
- date_from/date_to: 如果用户提到明确或相对时间，换算成 ISO 日期或日期时间；没有则为 null。
- directory_query: 如果用户提到文件夹、相册或目录名，填对应文字；没有则为 null。
"""
    try:
        raw = await ollama.generate_text_json(
            model=model_name,
            prompt=prompt,
            schema=AI_SEARCH_INTENT_SCHEMA,
            system_prompt=AI_SEARCH_SYSTEM_PROMPT,
        )
    except Exception:
        return _fallback_intent(request)
    return _normalize_intent(raw, request)


def _fallback_intent(request: SearchRequest) -> AiIntent:
    media_type = request.media_type
    if media_type == "any":
        if _VIDEO_RE.search(request.query):
            media_type = "video"
        elif _IMAGE_RE.search(request.query):
            media_type = "image"
    task = "summarize" if re.search(r"总结|概括|汇总|这个文件夹|文件夹里", request.query) else "find"
    return AiIntent(
        task=task,
        media_type=media_type,
        semantic_query=request.query,
        date_from=request.date_from,
        date_to=request.date_to,
    )


def _normalize_intent(raw: dict[str, Any], request: SearchRequest) -> AiIntent:
    task = str(raw.get("task") or "").strip()
    if task not in {"find", "recommend", "summarize"}:
        task = "find"
    media_type = str(raw.get("media_type") or request.media_type).strip()
    if media_type not in {"image", "video", "any"}:
        media_type = request.media_type
    semantic_query = _clean_text(raw.get("semantic_query")) or request.query
    return AiIntent(
        task=task,
        media_type=media_type,
        semantic_query=semantic_query,
        date_from=_parse_datetime(raw.get("date_from"), end_of_day=False),
        date_to=_parse_datetime(raw.get("date_to"), end_of_day=True),
        directory_query=_clean_text(raw.get("directory_query")) or None,
    )


def _parsed_filters_from_intent(request: SearchRequest, intent: AiIntent) -> ParsedFilters:
    media_type = request.media_type if request.media_type != "any" else intent.media_type
    if media_type == "any":
        if _VIDEO_RE.search(request.query):
            media_type = "video"
        elif _IMAGE_RE.search(request.query):
            media_type = "image"
    return ParsedFilters(
        media_type=media_type,
        date_from=request.date_from or intent.date_from,
        date_to=request.date_to or intent.date_to,
        semantic_query=intent.semantic_query or request.query,
    )


def _load_ai_scope(
    db: Session,
    request: SearchRequest,
    parsed: ParsedFilters,
    intent: AiIntent,
    directory_hints: list[dict[str, str]],
) -> list[tuple[MediaFile, MediaAiSummary]]:
    stmt = (
        select(MediaFile, MediaAiSummary)
        .join(MediaAiSummary, MediaAiSummary.media_id == MediaFile.id)
        .where(MediaFile.status == "done", visible_media_filter(db))
        .order_by(MediaFile.captured_at.is_(None), MediaFile.captured_at.desc(), MediaFile.created_at.desc())
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
    if request.directory_path:
        stmt = stmt.where(_directory_filter(request.directory_path))
    elif intent.directory_query:
        paths = _resolve_directory_paths(intent.directory_query, directory_hints)
        if paths:
            stmt = stmt.where(or_(*[_directory_filter(path) for path in paths[:20]]))
    return list(db.execute(stmt).all())


async def _rank_candidates(
    db: Session,
    ollama: OllamaClient,
    request: SearchRequest,
    semantic_query: str,
    candidates: list[AiCandidate],
) -> list[AiCandidate]:
    embeddings = _embedding_map(db, candidates)
    query_vector: list[float] | None = None
    if embeddings:
        try:
            query_vector = await ollama.embed_text(model=get_settings().default_embedding_model, text=semantic_query)
        except Exception:
            query_vector = None

    ranked: list[AiCandidate] = []
    for candidate in candidates:
        media_id = candidate.media.id
        vector_score = 0.0
        if query_vector is not None:
            vector_score = max(0.0, min(1.0, cosine_similarity(query_vector, embeddings.get(media_id))))
        text_score = keyword_score(request.query, candidate.summary.searchable_text or "")
        ranked.append(
            AiCandidate(
                media=candidate.media,
                summary=candidate.summary,
                vector_score=vector_score,
                keyword_score=text_score,
            )
        )
    return sorted(ranked, key=lambda item: item.rank_score, reverse=True)


def _embedding_map(db: Session, candidates: list[AiCandidate]) -> dict[object, list[float]]:
    model_name = get_settings().default_embedding_model.strip()
    if not model_name:
        return {}
    profile = db.scalar(select(EmbeddingProfile).where(EmbeddingProfile.model_name == model_name))
    if profile is None:
        return {}
    media_ids = [candidate.media.id for candidate in candidates]
    if not media_ids:
        return {}
    rows = db.execute(
        select(MediaEmbedding.media_id, MediaEmbedding.embedding).where(
            MediaEmbedding.profile_id == profile.id,
            MediaEmbedding.media_id.in_(media_ids),
        )
    ).all()
    return {media_id: embedding for media_id, embedding in rows}


async def _ask_ai_over_candidates(
    *,
    ollama: OllamaClient,
    model_name: str,
    request: SearchRequest,
    intent: AiIntent,
    parsed: ParsedFilters,
    candidates: list[AiCandidate],
) -> dict[str, Any]:
    chunks = _pack_candidate_chunks(candidates, _AI_SEARCH_CHUNK_MAX_CHARS)
    if len(chunks) == 1:
        return await _ask_ai_for_chunk(
            ollama=ollama,
            model_name=model_name,
            request=request,
            intent=intent,
            parsed=parsed,
            candidates=chunks[0],
            batch_label="全部候选",
        )

    partials: list[dict[str, Any]] = []
    selected_ids: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        raw = await _ask_ai_for_chunk(
            ollama=ollama,
            model_name=model_name,
            request=request,
            intent=intent,
            parsed=parsed,
            candidates=chunk,
            batch_label=f"第 {index}/{len(chunks)} 批候选",
        )
        partials.append(raw)
        for item in raw.get("results") or []:
            media_id = _clean_text(item.get("media_id")) if isinstance(item, dict) else ""
            if media_id:
                selected_ids.append(media_id)

    selected_set = set(selected_ids)
    merge_candidates = [candidate for candidate in candidates if str(candidate.media.id) in selected_set]
    if not merge_candidates:
        merge_candidates = candidates[: request.limit]
    return await _ask_ai_to_merge_partials(
        ollama=ollama,
        model_name=model_name,
        request=request,
        intent=intent,
        parsed=parsed,
        partials=partials,
        candidates=merge_candidates[: max(request.limit, 30)],
    )


async def _ask_ai_for_chunk(
    *,
    ollama: OllamaClient,
    model_name: str,
    request: SearchRequest,
    intent: AiIntent,
    parsed: ParsedFilters,
    candidates: list[AiCandidate],
    batch_label: str,
) -> dict[str, Any]:
    prompt = f"""用户请求：
{request.query}

解析后的任务：
{json.dumps(_intent_payload(intent, parsed), ensure_ascii=False)}

当前候选范围：{batch_label}，共 {len(candidates)} 条。请只根据下面的媒体描述回答。

输出要求：
- answer 用简体中文回答用户问题；如果是总结任务，要概括这个范围的共同主题、时间/场景线索和值得注意的内容。
- results 最多返回 {request.limit} 条最相关或最有代表性的媒体。
- media_id 必须来自候选列表，不能编造。
- 如果没有合适媒体，results 返回空数组，并在 answer 说明。

候选媒体：
{_candidates_text(candidates)}
"""
    return await ollama.generate_text_json(
        model=model_name,
        prompt=prompt,
        schema=AI_SEARCH_RESPONSE_SCHEMA,
        system_prompt=AI_SEARCH_SYSTEM_PROMPT,
    )


async def _ask_ai_to_merge_partials(
    *,
    ollama: OllamaClient,
    model_name: str,
    request: SearchRequest,
    intent: AiIntent,
    parsed: ParsedFilters,
    partials: list[dict[str, Any]],
    candidates: list[AiCandidate],
) -> dict[str, Any]:
    partial_text = "\n\n".join(
        f"批次 {index}：{_clean_text(item.get('answer')) or '无摘要'}"
        for index, item in enumerate(partials, start=1)
    )
    prompt = f"""用户请求：
{request.query}

解析后的任务：
{json.dumps(_intent_payload(intent, parsed), ensure_ascii=False)}

以下是对大范围媒体描述分批阅读后的摘要，请整合成最终回答。

分批摘要：
{partial_text}

可引用的代表媒体：
{_candidates_text(candidates)}

输出要求：
- answer 要覆盖所有分批摘要，不要只总结代表媒体。
- results 最多返回 {request.limit} 条代表媒体，media_id 必须来自上面的代表媒体列表。
"""
    return await ollama.generate_text_json(
        model=model_name,
        prompt=prompt,
        schema=AI_SEARCH_RESPONSE_SCHEMA,
        system_prompt=AI_SEARCH_SYSTEM_PROMPT,
    )


def _validated_result_items(
    raw: dict[str, Any],
    candidates: list[AiCandidate],
    limit: int,
) -> list[SearchResultItem]:
    by_id = {str(candidate.media.id): candidate for candidate in candidates}
    seen: set[str] = set()
    results: list[SearchResultItem] = []
    for item in raw.get("results") or []:
        if not isinstance(item, dict):
            continue
        media_id = _clean_text(item.get("media_id"))
        if not media_id or media_id in seen or media_id not in by_id:
            continue
        candidate = by_id[media_id]
        seen.add(media_id)
        results.append(
            SearchResultItem(
                media_id=candidate.media.id,
                path=candidate.media.path,
                thumbnail_url=f"/api/media/{candidate.media.id}/thumbnail",
                media_type=candidate.media.media_type,
                captured_at=candidate.media.captured_at,
                title=candidate.summary.title,
                short_summary=candidate.summary.short_summary,
                match_reason=_clean_text(item.get("reason")) or "AI 判断与请求相关",
                score=_clamp_score(item.get("score")),
            )
        )
    results.sort(key=lambda item: item.score, reverse=True)
    return results[:limit]


def _pack_candidate_chunks(candidates: list[AiCandidate], max_chars: int) -> list[list[AiCandidate]]:
    chunks: list[list[AiCandidate]] = []
    current: list[AiCandidate] = []
    current_size = 0
    for candidate in candidates:
        item_size = len(_candidate_text(candidate))
        if current and current_size + item_size > max_chars:
            chunks.append(current)
            current = []
            current_size = 0
        current.append(candidate)
        current_size += item_size
    if current:
        chunks.append(current)
    return chunks


def _candidates_text(candidates: list[AiCandidate]) -> str:
    return "\n\n".join(_candidate_text(candidate) for candidate in candidates)


def _candidate_text(candidate: AiCandidate) -> str:
    media = candidate.media
    summary = candidate.summary
    text = _clip(summary.searchable_text or summary.short_summary or "", 1200)
    captured_at = media.captured_at.isoformat() if media.captured_at else "unknown"
    return f"""ID: {media.id}
Type: {media.media_type}
Time: {captured_at}
Directory: {media.parent_dir or ""}
Path: {media.path}
Title: {summary.title or ""}
Vector score: {candidate.vector_score:.3f}
Keyword score: {candidate.keyword_score:.3f}
Description:
{text}"""


def _intent_payload(intent: AiIntent, parsed: ParsedFilters) -> dict[str, Any]:
    return {
        "task": intent.task,
        "media_type": parsed.media_type,
        "semantic_query": parsed.semantic_query,
        "date_from": parsed.date_from.isoformat() if parsed.date_from else None,
        "date_to": parsed.date_to.isoformat() if parsed.date_to else None,
        "directory_query": intent.directory_query,
    }


def _directory_hints(db: Session) -> list[dict[str, str]]:
    hints: list[dict[str, str]] = []
    for rule in db.scalars(select(DirectoryRule).where(DirectoryRule.enabled)).all():
        hints.append({"name": _directory_name(rule.path), "path": rule.normalized_path, "display_path": rule.path})

    rows = db.execute(
        select(MediaFile.parent_dir, func.count(MediaFile.id))
        .where(visible_media_filter(db), MediaFile.parent_dir.is_not(None))
        .group_by(MediaFile.parent_dir)
        .order_by(func.count(MediaFile.id).desc())
        .limit(_DIRECTORY_HINT_LIMIT)
    ).all()
    seen = {item["path"] for item in hints}
    for parent_dir, _count in rows:
        if not parent_dir or parent_dir in seen:
            continue
        hints.append({"name": _directory_name(parent_dir), "path": parent_dir, "display_path": parent_dir})
        seen.add(parent_dir)
    return hints[:_DIRECTORY_HINT_LIMIT]


def _resolve_directory_paths(directory_query: str, hints: list[dict[str, str]]) -> list[str]:
    query = directory_query.strip().lower()
    if not query:
        return []
    normalized_query = normalize_path(directory_query).lower()
    matches: list[str] = []
    for item in hints:
        haystack = " ".join(
            [
                item.get("name", ""),
                item.get("path", ""),
                item.get("display_path", ""),
            ]
        ).lower()
        if query in haystack or normalized_query in haystack:
            matches.append(item["path"])
    return matches


def _directory_filter(directory_path: str):
    normalized = normalize_path(directory_path)
    return or_(
        MediaFile.parent_dir == normalized,
        MediaFile.parent_dir.like(f"{_escape_like(normalized)}/%", escape="\\"),
        MediaFile.root_path == normalized,
    )


def _parse_datetime(value: object, *, end_of_day: bool) -> datetime | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            parsed_date = date.fromisoformat(text)
            parsed_time = time.max if end_of_day else time.min
            return datetime.combine(parsed_date, parsed_time)
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


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


def _clamp_score(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(1.0, number)), 6)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _directory_name(path: str) -> str:
    normalized = path.replace("\\", "/").rstrip("/")
    if not normalized:
        return path
    if re.fullmatch(r"[A-Za-z]:", normalized):
        return normalized.upper()
    return normalized.rsplit("/", 1)[-1] or normalized
