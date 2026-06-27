from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.db_models import DirectoryRule, MediaAiSummary, MediaFile, VideoSegmentSummary
from app.services import ai_analyzer
from app.services.ai_analyzer import analyze_video, regenerate_video_final_summary
from app.services.video_frame_extractor import ExtractedFrame


class FakeOllama:
    def __init__(self) -> None:
        self.vision_models: list[str] = []
        self.vision_prompts: list[str] = []
        self.vision_system_prompts: list[str] = []
        self.vision_image_paths: list[list[str]] = []
        self.summary_model: str | None = None
        self.summary_prompt: str | None = None
        self.summary_system_prompt: str | None = None

    async def generate_vision_batch_json(self, **kwargs):
        self.vision_models.append(kwargs["model"])
        self.vision_prompts.append(kwargs["prompt"])
        self.vision_system_prompts.append(kwargs["system_prompt"])
        self.vision_image_paths.append(kwargs["image_paths"])
        index = len(self.vision_prompts)
        return {
            "current_segment_summary": f"segment {index} summary",
            "important_observations": [f"observation-{index}"],
            "updated_global_summary": f"global after segment {index}",
            "uncertain_points": [f"uncertain-{index}"] if index == 2 else [],
            "current_segment_tags": [f"tag-{index}"],
            "important_objects": [f"object-{index}"],
            "new_objects_or_scenes": [f"scene-{index}"],
            "confidence": 0.8,
        }

    async def generate_text_json(self, **kwargs):
        self.summary_model = kwargs["model"]
        self.summary_prompt = kwargs["prompt"]
        self.summary_system_prompt = kwargs["system_prompt"]
        return {
            "title": "final video title",
            "short_summary": "final video summary",
            "detailed_summary": "final detailed summary",
            "timeline": [
                {"start_time": "00:00:00", "end_time": "00:00:10", "summary": "whole video"}
            ],
            "scene": "final scene",
            "objects": ["object-1", "object-2"],
            "actions": ["tag-1", "tag-2"],
            "uncertain_points": ["uncertain-2"],
            "search_keywords": ["final video title", "tag-1"],
            "confidence": "high",
        }


class FakeFinalOnlyOllama:
    def __init__(self) -> None:
        self.summary_model: str | None = None
        self.summary_prompt: str | None = None

    async def generate_vision_batch_json(self, **kwargs):
        raise AssertionError("final summary regeneration must not rerun vision segment analysis")

    async def generate_text_json(self, **kwargs):
        self.summary_model = kwargs["model"]
        self.summary_prompt = kwargs["prompt"]
        return {
            "title": "new final video title",
            "short_summary": "new final summary",
            "detailed_summary": "new final detailed summary",
            "timeline": [
                {"start_time": "00:00:00", "end_time": "00:00:10", "summary": "updated whole video"}
            ],
            "scene": "updated scene",
            "objects": ["updated object"],
            "actions": ["updated action"],
            "uncertain_points": [],
            "search_keywords": ["new final video title"],
            "confidence": "high",
        }


def test_analyze_video_uses_recursive_summary_for_segments(monkeypatch, tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    def fake_extract_video_frames(*args, **kwargs):
        return [
            ExtractedFrame(timestamp_seconds=0.0, frame_path=str(tmp_path / "frame-1.jpg")),
            ExtractedFrame(timestamp_seconds=5.0, frame_path=str(tmp_path / "frame-2.jpg")),
            ExtractedFrame(timestamp_seconds=10.0, frame_path=str(tmp_path / "frame-3.jpg")),
        ]

    monkeypatch.setattr(ai_analyzer, "extract_video_frames", fake_extract_video_frames)
    monkeypatch.setattr(
        ai_analyzer,
        "get_default_video_segment_system_prompt",
        lambda: "global segment system prompt",
    )
    monkeypatch.setattr(
        ai_analyzer,
        "get_default_video_final_summary_system_prompt",
        lambda: "global final system prompt",
    )

    with SessionLocal() as db:
        rule = DirectoryRule(
            path=str(tmp_path),
            normalized_path=str(tmp_path).lower(),
            recursive=True,
            vision_model="vision-model",
            summary_model="summary-model",
            video_batch_size=2,
            video_batch_overlap=1,
            video_segment_prompt="directory segment prompt",
            video_final_summary_prompt="directory final prompt",
            background_context="directory-only-context",
            background_context_prompt="directory-only-rule",
            video_frame_strategy="fixed_interval",
            frame_interval_seconds=5,
            max_frames_per_video=12,
            video_frame_max_width=1280,
            analysis_detail="normal",
            enabled=True,
        )
        media = MediaFile(
            path=str(tmp_path / "video.mp4"),
            normalized_path=str(tmp_path / "video.mp4").lower(),
            root_path=str(tmp_path).lower(),
            parent_dir=str(tmp_path),
            media_type="video",
            duration_seconds=10.0,
            status="pending",
            folder_rule=rule,
            background_context="media-only-context",
        )
        db.add_all([rule, media])
        db.commit()
        db.refresh(media)

        fake_ollama = FakeOllama()
        progress_updates: list[tuple[str, int, int]] = []
        summary = asyncio.run(
            analyze_video(
                db,
                media,
                fake_ollama,
                progress_callback=lambda stage, current, total: progress_updates.append(
                    (stage, current, total)
                ),
            )
        )

        segments = list(db.scalars(select(VideoSegmentSummary).order_by(VideoSegmentSummary.segment_index)))
        db.refresh(media)

    assert fake_ollama.vision_models == ["vision-model", "vision-model", "vision-model"]
    assert fake_ollama.vision_system_prompts == [
        "global segment system prompt",
        "global segment system prompt",
        "global segment system prompt",
    ]
    assert fake_ollama.vision_image_paths == [
        [str(tmp_path / "frame-1.jpg"), str(tmp_path / "frame-2.jpg")],
        [str(tmp_path / "frame-2.jpg"), str(tmp_path / "frame-3.jpg")],
        [str(tmp_path / "frame-3.jpg")],
    ]
    assert "暂无历史信息" in fake_ollama.vision_prompts[0]
    assert "media-only-context" in fake_ollama.vision_prompts[0]
    assert "directory-only-rule" in fake_ollama.vision_prompts[0]
    assert "directory-only-context\nmedia-only-context" in fake_ollama.vision_prompts[0]
    assert '"source_filename":"video.mp4"' in fake_ollama.vision_prompts[0]
    assert str(tmp_path) not in fake_ollama.vision_prompts[0]
    assert "global after segment 1" in fake_ollama.vision_prompts[1]
    assert "segment 1 summary" not in fake_ollama.vision_prompts[1]
    assert "events" not in fake_ollama.vision_prompts[1]
    assert "ocr_text" not in fake_ollama.vision_prompts[1]
    assert fake_ollama.summary_model == "summary-model"
    assert fake_ollama.summary_system_prompt == "global final system prompt"
    assert fake_ollama.summary_prompt is not None
    assert "segment 1 summary" in fake_ollama.summary_prompt
    assert "segment 2 summary" in fake_ollama.summary_prompt
    assert "media-only-context" in fake_ollama.summary_prompt
    assert "directory-only-rule" in fake_ollama.summary_prompt
    assert "directory-only-context\nmedia-only-context" in fake_ollama.summary_prompt
    assert '"source_filename":"video.mp4"' in fake_ollama.summary_prompt
    assert str(tmp_path) not in fake_ollama.summary_prompt
    assert "global after segment 3" in fake_ollama.summary_prompt
    assert "rolling_global_summary" not in fake_ollama.summary_prompt
    assert "updated_timeline" not in fake_ollama.summary_prompt
    assert summary.model_used == "summary-model"
    assert summary.short_summary == "final video summary"
    assert summary.text_visible == []
    assert media.status == "embedding_pending"
    assert len(segments) == 3
    assert segments[0].current_segment_summary == "segment 1 summary"
    assert segments[1].important_observations == ["observation-2"]
    assert segments[1].uncertain_points == ["uncertain-2"]
    assert ("extract_frames", 0, 0) in progress_updates
    assert ("analyze_segments", 1, 3) in progress_updates
    assert ("analyze_segments", 2, 3) in progress_updates
    assert ("analyze_segments", 3, 3) in progress_updates
    assert progress_updates[-1] == ("final_summary", 3, 3)


def test_analyze_video_resume_starts_after_saved_segments(monkeypatch, tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    frame_paths = [str(tmp_path / f"frame-{index}.jpg") for index in range(1, 5)]

    def fake_extract_video_frames(*args, **kwargs):
        return [
            ExtractedFrame(timestamp_seconds=0.0, frame_path=frame_paths[0]),
            ExtractedFrame(timestamp_seconds=5.0, frame_path=frame_paths[1]),
            ExtractedFrame(timestamp_seconds=10.0, frame_path=frame_paths[2]),
            ExtractedFrame(timestamp_seconds=15.0, frame_path=frame_paths[3]),
        ]

    monkeypatch.setattr(ai_analyzer, "extract_video_frames", fake_extract_video_frames)

    with SessionLocal() as db:
        rule = DirectoryRule(
            path=str(tmp_path),
            normalized_path=str(tmp_path).lower(),
            recursive=True,
            vision_model="vision-model",
            summary_model="summary-model",
            video_batch_size=2,
            video_batch_overlap=0,
            video_frame_strategy="fixed_interval",
            frame_interval_seconds=5,
            max_frames_per_video=12,
            video_frame_max_width=1280,
            analysis_detail="normal",
            enabled=True,
        )
        media = MediaFile(
            path=str(tmp_path / "video.mp4"),
            normalized_path=str(tmp_path / "video.mp4").lower(),
            root_path=str(tmp_path).lower(),
            parent_dir=str(tmp_path),
            media_type="video",
            duration_seconds=20.0,
            status="failed",
            error_message="previous failure",
            folder_rule=rule,
        )
        db.add_all([rule, media])
        db.flush()
        db.add(
            VideoSegmentSummary(
                media_id=media.id,
                segment_index=1,
                start_time_seconds=0.0,
                end_time_seconds=5.0,
                current_segment_summary="saved first segment",
                important_observations=["saved observation"],
                updated_global_summary="global after saved first segment",
                current_segment_tags=["saved tag"],
                important_objects=["saved object"],
                new_objects_or_scenes=["saved scene"],
                confidence=0.7,
            )
        )
        db.commit()
        db.refresh(media)

        fake_ollama = FakeOllama()
        progress_updates: list[tuple[str, int, int]] = []
        summary = asyncio.run(
            analyze_video(
                db,
                media,
                fake_ollama,
                progress_callback=lambda stage, current, total: progress_updates.append(
                    (stage, current, total)
                ),
                resume_existing_segments=True,
            )
        )

        segments = list(db.scalars(select(VideoSegmentSummary).order_by(VideoSegmentSummary.segment_index)))
        db.refresh(media)

    assert fake_ollama.vision_image_paths == [[frame_paths[2], frame_paths[3]]]
    assert fake_ollama.vision_prompts
    assert "global after saved first segment" in fake_ollama.vision_prompts[0]
    assert '"source_filename":"video.mp4"' in fake_ollama.vision_prompts[0]
    assert len(segments) == 2
    assert segments[0].current_segment_summary == "saved first segment"
    assert segments[1].segment_index == 2
    assert segments[1].current_segment_summary == "segment 1 summary"
    assert fake_ollama.summary_prompt is not None
    assert "saved first segment" in fake_ollama.summary_prompt
    assert "segment 1 summary" in fake_ollama.summary_prompt
    assert '"source_filename":"video.mp4"' in fake_ollama.summary_prompt
    assert summary.short_summary == "final video summary"
    assert media.status == "embedding_pending"
    assert ("analyze_segments", 1, 2) in progress_updates
    assert ("analyze_segments", 2, 2) in progress_updates


def test_regenerate_video_final_summary_uses_saved_segments(tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        rule = DirectoryRule(
            path=str(tmp_path),
            normalized_path=str(tmp_path).lower(),
            recursive=True,
            vision_model="vision-model",
            summary_model="summary-model",
            video_batch_size=2,
            video_batch_overlap=1,
            video_frame_strategy="fixed_interval",
            frame_interval_seconds=5,
            max_frames_per_video=12,
            video_frame_max_width=1280,
            analysis_detail="normal",
            enabled=True,
        )
        media = MediaFile(
            path=str(tmp_path / "video.mp4"),
            normalized_path=str(tmp_path / "video.mp4").lower(),
            root_path=str(tmp_path).lower(),
            parent_dir=str(tmp_path),
            media_type="video",
            duration_seconds=10.0,
            status="done",
            folder_rule=rule,
        )
        db.add_all([rule, media])
        db.flush()
        db.add_all(
            [
                VideoSegmentSummary(
                    media_id=media.id,
                    segment_index=1,
                    start_time_seconds=0.0,
                    end_time_seconds=5.0,
                    current_segment_summary="first saved segment",
                    updated_global_summary="global after first",
                    current_segment_tags=["tag-1"],
                    important_objects=["object-1"],
                    new_objects_or_scenes=["scene-1"],
                    confidence=0.8,
                ),
                VideoSegmentSummary(
                    media_id=media.id,
                    segment_index=2,
                    start_time_seconds=5.0,
                    end_time_seconds=10.0,
                    current_segment_summary="second saved segment",
                    updated_global_summary="global after second",
                    current_segment_tags=["tag-2"],
                    important_objects=["object-2"],
                    new_objects_or_scenes=["scene-2"],
                    confidence=0.9,
                ),
                MediaAiSummary(
                    media_id=media.id,
                    model_used="old-summary-model",
                    title="old title",
                    short_summary="old summary",
                    detailed_summary="old detailed summary",
                    objects=[],
                    people=[],
                    actions=[],
                    text_visible=[],
                    search_keywords=[],
                    searchable_text="old searchable text",
                    raw_json={"final_global_summary": "old global summary"},
                ),
            ]
        )
        db.commit()
        db.refresh(media)

        fake_ollama = FakeFinalOnlyOllama()
        summary = asyncio.run(regenerate_video_final_summary(db, media, fake_ollama))
        db.refresh(media)

    assert fake_ollama.summary_model == "summary-model"
    assert fake_ollama.summary_prompt is not None
    assert "first saved segment" in fake_ollama.summary_prompt
    assert "second saved segment" in fake_ollama.summary_prompt
    assert '"source_filename":"video.mp4"' in fake_ollama.summary_prompt
    assert summary.short_summary == "new final summary"
    assert summary.model_used == "summary-model"
    assert media.status == "embedding_pending"
