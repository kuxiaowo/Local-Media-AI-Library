from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.types import GUID


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DirectoryRule(Base, TimestampMixin):
    __tablename__ = "directory_rules"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_path: Mapped[str] = mapped_column(String(768), nullable=False, unique=True)
    recursive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    vision_model: Mapped[str] = mapped_column(Text, nullable=False)
    summary_model: Mapped[str] = mapped_column(Text, nullable=False)
    custom_analysis_prompt: Mapped[str | None] = mapped_column(Text)
    background_context: Mapped[str | None] = mapped_column(Text)
    background_context_prompt: Mapped[str | None] = mapped_column(Text)
    video_segment_prompt: Mapped[str | None] = mapped_column(Text)
    video_final_summary_prompt: Mapped[str | None] = mapped_column(Text)
    video_frame_strategy: Mapped[str] = mapped_column(Text, nullable=False, default="hybrid")
    frame_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    max_frames_per_video: Mapped[int] = mapped_column(Integer, nullable=False, default=12)
    video_frame_max_width: Mapped[int] = mapped_column(Integer, nullable=False, default=1280)
    video_frame_max_height: Mapped[int | None] = mapped_column(Integer)
    video_batch_size: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    video_batch_overlap: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    analysis_detail: Mapped[str] = mapped_column(Text, nullable=False, default="normal")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    media_files: Mapped[list[MediaFile]] = relationship("MediaFile", back_populates="folder_rule")


class MediaFile(Base, TimestampMixin):
    __tablename__ = "media_files"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_path: Mapped[str] = mapped_column(String(768), nullable=False, unique=True)
    root_path: Mapped[str | None] = mapped_column(String(768))
    parent_dir: Mapped[str | None] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(Text)
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    file_hash: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    captured_at_source: Mapped[str | None] = mapped_column(Text)
    captured_at_confidence: Mapped[str | None] = mapped_column(Text)
    file_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    file_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    folder_rule_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("directory_rules.id"))
    resolved_config_hash: Mapped[str | None] = mapped_column(Text)
    background_context: Mapped[str | None] = mapped_column(Text)
    background_context_prompt: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    error_message: Mapped[str | None] = mapped_column(Text)
    thumbnail_path: Mapped[str | None] = mapped_column(Text)

    folder_rule: Mapped[DirectoryRule | None] = relationship("DirectoryRule", back_populates="media_files")
    ai_summary: Mapped[MediaAiSummary | None] = relationship(
        "MediaAiSummary", back_populates="media", cascade="all, delete-orphan", uselist=False
    )
    video_frames: Mapped[list[VideoFrameSummary]] = relationship(
        "VideoFrameSummary",
        back_populates="media",
        cascade="all, delete-orphan",
        order_by="VideoFrameSummary.timestamp_seconds",
    )
    video_segments: Mapped[list[VideoSegmentSummary]] = relationship(
        "VideoSegmentSummary",
        back_populates="media",
        cascade="all, delete-orphan",
        order_by="VideoSegmentSummary.segment_index",
    )
    embeddings: Mapped[list[MediaEmbedding]] = relationship(
        "MediaEmbedding", back_populates="media", cascade="all, delete-orphan"
    )


class MediaAiSummary(Base, TimestampMixin):
    __tablename__ = "media_ai_summaries"

    media_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("media_files.id", ondelete="CASCADE"), primary_key=True
    )
    model_used: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    short_summary: Mapped[str | None] = mapped_column(Text)
    detailed_summary: Mapped[str | None] = mapped_column(Text)
    scene: Mapped[str | None] = mapped_column(Text)
    objects: Mapped[list | dict | None] = mapped_column(JSON)
    people: Mapped[list | dict | None] = mapped_column(JSON)
    actions: Mapped[list | dict | None] = mapped_column(JSON)
    text_visible: Mapped[list | dict | None] = mapped_column(JSON)
    location_guess: Mapped[str | None] = mapped_column(Text)
    time_clues: Mapped[str | None] = mapped_column(Text)
    mood: Mapped[str | None] = mapped_column(Text)
    search_keywords: Mapped[list | dict | None] = mapped_column(JSON)
    searchable_text: Mapped[str] = mapped_column(Text, nullable=False)
    raw_json: Mapped[dict | None] = mapped_column(JSON)
    confidence: Mapped[str | None] = mapped_column(Text)

    media: Mapped[MediaFile] = relationship("MediaFile", back_populates="ai_summary")


class VideoFrameSummary(Base):
    __tablename__ = "video_frame_summaries"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    media_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("media_files.id", ondelete="CASCADE"), nullable=False
    )
    segment_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("video_segment_summaries.id", ondelete="SET NULL")
    )
    frame_index: Mapped[int | None] = mapped_column(Integer)
    timestamp_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    frame_path: Mapped[str] = mapped_column(Text, nullable=False)
    model_used: Mapped[str | None] = mapped_column(Text)
    caption: Mapped[str | None] = mapped_column(Text)
    objects: Mapped[list | dict | None] = mapped_column(JSON)
    people: Mapped[list | dict | None] = mapped_column(JSON)
    actions: Mapped[list | dict | None] = mapped_column(JSON)
    text_visible: Mapped[list | dict | None] = mapped_column(JSON)
    raw_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    media: Mapped[MediaFile] = relationship("MediaFile", back_populates="video_frames")
    segment: Mapped[VideoSegmentSummary | None] = relationship("VideoSegmentSummary", back_populates="frames")


class VideoSegmentSummary(Base):
    __tablename__ = "video_segment_summaries"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    media_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("media_files.id", ondelete="CASCADE"), nullable=False
    )
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time_seconds: Mapped[float | None] = mapped_column(Float)
    end_time_seconds: Mapped[float | None] = mapped_column(Float)
    frame_paths: Mapped[list | dict | None] = mapped_column(JSON)
    current_segment_summary: Mapped[str | None] = mapped_column(Text)
    important_observations: Mapped[list | dict | None] = mapped_column(JSON)
    current_segment_tags: Mapped[list | dict | None] = mapped_column(JSON)
    important_objects: Mapped[list | dict | None] = mapped_column(JSON)
    ocr_text: Mapped[list | dict | None] = mapped_column(JSON)
    new_objects_or_scenes: Mapped[list | dict | None] = mapped_column(JSON)
    updated_global_summary: Mapped[str | None] = mapped_column(Text)
    updated_timeline: Mapped[list | dict | None] = mapped_column(JSON)
    uncertain_points: Mapped[list | dict | None] = mapped_column(JSON)
    confidence: Mapped[float | None] = mapped_column(Float)
    raw_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    media: Mapped[MediaFile] = relationship("MediaFile", back_populates="video_segments")
    frames: Mapped[list[VideoFrameSummary]] = relationship(
        "VideoFrameSummary",
        back_populates="segment",
        order_by="VideoFrameSummary.frame_index",
    )


class EmbeddingProfile(Base):
    __tablename__ = "embedding_profiles"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    embeddings: Mapped[list[MediaEmbedding]] = relationship("MediaEmbedding", back_populates="profile")


class MediaEmbedding(Base, TimestampMixin):
    __tablename__ = "media_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    media_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("media_files.id", ondelete="CASCADE"), nullable=False
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("embedding_profiles.id"), nullable=False
    )
    embedding: Mapped[list[float]] = mapped_column(JSON, nullable=False)
    embedded_text: Mapped[str] = mapped_column(Text, nullable=False)

    media: Mapped[MediaFile] = relationship("MediaFile", back_populates="embeddings")
    profile: Mapped[EmbeddingProfile] = relationship("EmbeddingProfile", back_populates="embeddings")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    job_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    target_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    target_path: Mapped[str | None] = mapped_column(Text)
    progress_current: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
