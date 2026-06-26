from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DirectoryRuleBase(BaseModel):
    path: str
    recursive: bool = True
    vision_model: str
    summary_model: str
    custom_analysis_prompt: str | None = ""
    background_context: str | None = ""
    background_context_prompt: str | None = ""
    video_segment_prompt: str | None = ""
    video_final_summary_prompt: str | None = ""
    video_frame_strategy: Literal["fixed_interval", "scene", "hybrid"] = "hybrid"
    frame_interval_seconds: int = Field(default=5, ge=1)
    max_frames_per_video: int = Field(default=12, ge=1, le=500)
    video_frame_max_width: int = Field(default=1280, ge=160, le=4096)
    video_frame_max_height: int | None = Field(default=None, ge=160, le=4096)
    video_batch_size: int = Field(default=6, ge=1, le=24)
    video_batch_overlap: int = Field(default=1, ge=0, le=23)
    analysis_detail: str = "normal"
    enabled: bool = True


class DirectoryRuleCreate(DirectoryRuleBase):
    pass


class DirectoryRuleUpdate(BaseModel):
    path: str | None = None
    recursive: bool | None = None
    vision_model: str | None = None
    summary_model: str | None = None
    custom_analysis_prompt: str | None = None
    background_context: str | None = None
    background_context_prompt: str | None = None
    video_segment_prompt: str | None = None
    video_final_summary_prompt: str | None = None
    video_frame_strategy: Literal["fixed_interval", "scene", "hybrid"] | None = None
    frame_interval_seconds: int | None = Field(default=None, ge=1)
    max_frames_per_video: int | None = Field(default=None, ge=1, le=500)
    video_frame_max_width: int | None = Field(default=None, ge=160, le=4096)
    video_frame_max_height: int | None = Field(default=None, ge=160, le=4096)
    video_batch_size: int | None = Field(default=None, ge=1, le=24)
    video_batch_overlap: int | None = Field(default=None, ge=0, le=23)
    analysis_detail: str | None = None
    enabled: bool | None = None


class DirectoryRuleRead(DirectoryRuleBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    normalized_path: str
    created_at: datetime
    updated_at: datetime


class MediaSummaryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    model_used: str
    title: str | None
    short_summary: str | None
    detailed_summary: str | None
    scene: str | None
    objects: Any
    people: Any
    actions: Any
    text_visible: Any
    location_guess: str | None
    time_clues: str | None
    mood: str | None
    search_keywords: Any
    searchable_text: str
    raw_json: Any
    confidence: str | None
    created_at: datetime
    updated_at: datetime


class VideoFrameSummaryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    segment_id: uuid.UUID | None
    frame_index: int | None
    timestamp_seconds: float
    frame_path: str
    model_used: str | None
    caption: str | None
    objects: Any
    people: Any
    actions: Any
    text_visible: Any
    raw_json: Any
    created_at: datetime


class VideoSegmentSummaryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    segment_index: int
    start_time_seconds: float | None
    end_time_seconds: float | None
    frame_paths: Any
    current_segment_summary: str | None
    important_observations: Any
    current_segment_tags: Any
    important_objects: Any
    new_objects_or_scenes: Any
    updated_global_summary: str | None
    uncertain_points: Any
    confidence: float | None
    raw_json: Any
    created_at: datetime


class MediaRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    path: str
    normalized_path: str
    root_path: str | None
    parent_dir: str | None
    media_type: str
    mime_type: str | None
    file_size: int | None
    file_hash: str | None
    width: int | None
    height: int | None
    duration_seconds: float | None
    captured_at: datetime | None
    captured_at_source: str | None
    captured_at_confidence: str | None
    file_created_at: datetime | None
    file_modified_at: datetime | None
    background_context: str | None
    status: str
    error_message: str | None
    thumbnail_path: str | None
    created_at: datetime
    updated_at: datetime
    ai_summary: MediaSummaryRead | None = None


class MediaDetailRead(MediaRead):
    video_frames: list[VideoFrameSummaryRead] = Field(default_factory=list)
    video_segments: list[VideoSegmentSummaryRead] = Field(default_factory=list)


class MediaBackgroundContextUpdate(BaseModel):
    background_context: str | None = ""


class MediaListResponse(BaseModel):
    items: list[MediaRead]
    total: int
    offset: int
    limit: int


class MediaDirectoryRead(BaseModel):
    path: str
    display_path: str
    name: str
    direct_media_count: int


class ScanStartRequest(BaseModel):
    directory_rule_id: uuid.UUID | None = None
    mode: Literal["incremental", "full"] = "incremental"


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_type: str
    status: str
    target_id: uuid.UUID | None
    target_path: str | None
    progress_current: int
    progress_total: int
    error_message: str | None
    payload: dict | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobClearResponse(BaseModel):
    deleted: int


class ScanStatusResponse(BaseModel):
    queued: int
    running: int
    failed: int
    completed: int
    media_total: int
    media_done: int
    media_failed: int
    media_missing: int


class MediaQueueItem(BaseModel):
    media_id: uuid.UUID
    path: str
    thumbnail_url: str | None
    media_status: str
    job_id: uuid.UUID | None
    job_type: str | None
    job_status: str | None
    job_progress_current: int
    job_progress_total: int
    job_payload: dict | None
    error_message: str | None
    updated_at: datetime
    job_created_at: datetime | None
    job_started_at: datetime | None


class MediaQueueResponse(BaseModel):
    items: list[MediaQueueItem]
    total: int


class SearchRequest(BaseModel):
    query: str
    mode: Literal["vector", "ai"] = "vector"
    media_type: Literal["image", "video", "any"] = "any"
    directory_rule_ids: list[uuid.UUID] = Field(default_factory=list)
    directory_path: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    limit: int = Field(default=30, ge=1, le=100)
    candidate_k: int = Field(default=100, ge=1, le=500)
    use_llm_rerank: bool = False


class ParsedFilters(BaseModel):
    media_type: str
    date_from: datetime | None
    date_to: datetime | None
    semantic_query: str


class SearchResultItem(BaseModel):
    media_id: uuid.UUID
    path: str
    thumbnail_url: str
    media_type: str
    captured_at: datetime | None
    title: str | None
    short_summary: str | None
    match_reason: str
    score: float


class SearchResponse(BaseModel):
    query: str
    mode: Literal["vector", "ai"] = "vector"
    parsed_filters: ParsedFilters
    results: list[SearchResultItem]
    answer: str | None = None
    ai_model: str | None = None
    scope_total: int | None = None


class OllamaStatusResponse(BaseModel):
    ok: bool
    base_url: str
    error: str | None = None


class OllamaModelsResponse(BaseModel):
    models: list[str]
