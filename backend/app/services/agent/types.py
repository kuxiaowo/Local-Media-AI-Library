from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class AgentEvent:
    event: str
    data: dict[str, Any]


@dataclass(frozen=True)
class RuntimeContext:
    now_iso: str
    today: str
    timezone: str = "Asia/Shanghai"
    locale: str = "zh-CN"
    recent_default_days: int = 30

    def to_prompt_payload(self) -> dict[str, Any]:
        return {
            "now_iso": self.now_iso,
            "today": self.today,
            "timezone": self.timezone,
            "locale": self.locale,
            "recent_default_days": self.recent_default_days,
        }


@dataclass(frozen=True)
class UiContext:
    page: str | None = None
    current_directory_path: str | None = None
    selected_media_ids: list[str] = field(default_factory=list)
    visible_media_ids: list[str] = field(default_factory=list)
    active_filters: dict[str, Any] = field(default_factory=dict)

    def to_prompt_payload(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "current_directory_path": self.current_directory_path,
            "selected_media_ids": self.selected_media_ids,
            "visible_media_ids": self.visible_media_ids,
            "active_filters": self.active_filters,
        }


@dataclass(frozen=True)
class LibraryRootContext:
    path: str
    display_name: str
    enabled: bool
    recursive: bool
    media_count: int = 0
    image_count: int = 0
    video_count: int = 0
    done_count: int = 0
    pending_count: int = 0
    failed_count: int = 0
    date_min: str | None = None
    date_max: str | None = None

    def to_prompt_payload(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class LibraryContext:
    roots: list[LibraryRootContext] = field(default_factory=list)
    folder_tree_summary: list[dict[str, Any]] = field(default_factory=list)

    def to_prompt_payload(self) -> dict[str, Any]:
        return {
            "roots": [root.to_prompt_payload() for root in self.roots],
            "folder_tree_summary": self.folder_tree_summary,
        }


@dataclass(frozen=True)
class ConversationContext:
    last_intent: str | None = None
    last_scope: dict[str, Any] | None = None
    last_shown_media_ids: list[str] = field(default_factory=list)
    last_selected_media_ids: list[str] = field(default_factory=list)
    last_answer_summary: str | None = None

    def to_prompt_payload(self) -> dict[str, Any]:
        return {
            "last_intent": self.last_intent,
            "last_scope": self.last_scope,
            "last_shown_media_ids": self.last_shown_media_ids,
            "last_selected_media_ids": self.last_selected_media_ids,
            "last_answer_summary": self.last_answer_summary,
        }


@dataclass(frozen=True)
class VisibleMemory:
    known_facts: list[str] = field(default_factory=list)
    checked_scopes: list[str] = field(default_factory=list)
    candidate_media_ids: list[str] = field(default_factory=list)
    rejected_scopes: list[str] = field(default_factory=list)

    def to_prompt_payload(self) -> dict[str, Any]:
        return {
            "known_facts": self.known_facts,
            "checked_scopes": self.checked_scopes,
            "candidate_media_ids": self.candidate_media_ids,
            "rejected_scopes": self.rejected_scopes,
        }


@dataclass(frozen=True)
class AgentContextPack:
    runtime_context: RuntimeContext
    library_context: LibraryContext
    ui_context: UiContext
    conversation_context: ConversationContext
    user_question: str = ""
    visible_memory: VisibleMemory = field(default_factory=VisibleMemory)
    library_overview: dict[str, Any] = field(default_factory=dict)
    directory_tree: list[dict[str, Any]] = field(default_factory=list)
    directory_stats: list[dict[str, Any]] = field(default_factory=list)
    read_description_pages: list[dict[str, Any]] = field(default_factory=list)
    candidate_media: list[dict[str, Any]] = field(default_factory=list)

    def planner_payload(self) -> dict[str, Any]:
        return {
            "current_time": self.runtime_context.to_prompt_payload(),
            "user_question": self.user_question,
            "visible_memory": self.visible_memory.to_prompt_payload(),
            "media_library_overview": self.library_overview,
            "directory_tree": self.directory_tree,
            "directory_stats": self.directory_stats,
            "read_description_pages": self.read_description_pages,
            "candidate_media": self.candidate_media,
            "runtime_context": self.runtime_context.to_prompt_payload(),
            "library_context": self.library_context.to_prompt_payload(),
            "ui_context": self.ui_context.to_prompt_payload(),
            "conversation_context": self.conversation_context.to_prompt_payload(),
        }


@dataclass(frozen=True)
class AgentPlan:
    task_type: str = "find"
    output_mode: str = "media_grid"
    needs_media_evidence: bool = True
    needs_visual_reinspection: bool = False
    should_show_media_grid: bool = True
    scope_reference: str = "global"
    media_type: str = "any"
    positive_requirements: list[str] = field(default_factory=list)
    negative_requirements: list[str] = field(default_factory=list)
    date_from: datetime | None = None
    date_to: datetime | None = None
    directory_hint: str | None = None
    semantic_query: str = ""
    clarification_question: str | None = None
    confidence: float = 0.5

    def to_payload(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "output_mode": self.output_mode,
            "needs_media_evidence": self.needs_media_evidence,
            "needs_visual_reinspection": self.needs_visual_reinspection,
            "should_show_media_grid": self.should_show_media_grid,
            "scope_reference": self.scope_reference,
            "media_type": self.media_type,
            "positive_requirements": self.positive_requirements,
            "negative_requirements": self.negative_requirements,
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "directory_hint": self.directory_hint,
            "semantic_query": self.semantic_query,
            "clarification_question": self.clarification_question,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class Scope:
    source: str = "global"
    media_type: str = "any"
    directory_paths: list[str] = field(default_factory=list)
    media_ids: list[uuid.UUID] = field(default_factory=list)
    date_from: datetime | None = None
    date_to: datetime | None = None
    analysis_status: str = "done"
    limit_hint: int = 30
    explain: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "media_type": self.media_type,
            "directory_paths": self.directory_paths,
            "media_ids": [str(media_id) for media_id in self.media_ids],
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "analysis_status": self.analysis_status,
            "limit_hint": self.limit_hint,
            "explain": self.explain,
        }


@dataclass(frozen=True)
class QueryExpansion:
    positive_queries: list[str] = field(default_factory=list)
    negative_queries: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "positive_queries": self.positive_queries,
            "negative_queries": self.negative_queries,
        }


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
    root_path: str | None = None

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
            "score": round(max(0.0, min(1.0, score if score is not None else self.score)), 6),
        }


@dataclass(frozen=True)
class JudgeResult:
    answer_type: str = "media_selection"
    text_answer: str = ""
    selected: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    stats: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5

    def to_payload(self) -> dict[str, Any]:
        return {
            "answer_type": self.answer_type,
            "text_answer": self.text_answer,
            "selected": self.selected,
            "rejected": self.rejected,
            "summary": self.summary,
            "stats": self.stats,
            "confidence": self.confidence,
        }
