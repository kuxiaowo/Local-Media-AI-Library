from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.db_models import DirectoryRule, MediaFile, VideoSegmentSummary
from app.services import ai_analyzer
from app.services.ai_analyzer import analyze_video
from app.services.video_frame_extractor import ExtractedFrame


class FakeOllama:
    def __init__(self) -> None:
        self.vision_models: list[str] = []
        self.vision_prompts: list[str] = []
        self.vision_system_prompts: list[str] = []
        self.summary_model: str | None = None
        self.summary_prompt: str | None = None
        self.summary_system_prompt: str | None = None

    async def generate_vision_batch_json(self, **kwargs):
        self.vision_models.append(kwargs["model"])
        self.vision_prompts.append(kwargs["prompt"])
        self.vision_system_prompts.append(kwargs["system_prompt"])
        index = len(self.vision_prompts)
        return {
            "current_segment_summary": f"segment {index} summary",
            "current_segment_tags": [f"tag-{index}"],
            "important_objects": [f"object-{index}"],
            "ocr_text": [f"ocr-{index}"],
            "new_objects_or_scenes": [f"scene-{index}"],
            "updated_global_summary": f"global after segment {index}",
            "updated_timeline": [
                {
                    "start_time": "00:00:00",
                    "end_time": f"00:00:0{index}",
                    "summary": f"timeline {index}",
                }
            ],
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
            "text_visible": ["ocr-1", "ocr-2"],
            "search_keywords": ["final video title", "tag-1"],
            "confidence": "high",
        }


def test_analyze_video_uses_summary_model_for_final_summary(monkeypatch, tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    def fake_extract_video_frames(*args, **kwargs):
        return [
            ExtractedFrame(timestamp_seconds=0.0, frame_path=str(tmp_path / "frame-1.jpg")),
            ExtractedFrame(timestamp_seconds=5.0, frame_path=str(tmp_path / "frame-2.jpg")),
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
            video_batch_size=1,
            video_segment_prompt="directory segment prompt",
            video_final_summary_prompt="directory final prompt",
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
        )
        db.add_all([rule, media])
        db.commit()
        db.refresh(media)

        fake_ollama = FakeOllama()
        summary = asyncio.run(analyze_video(db, media, fake_ollama))

        segments = list(db.scalars(select(VideoSegmentSummary).order_by(VideoSegmentSummary.segment_index)))

    assert fake_ollama.vision_models == ["vision-model", "vision-model"]
    assert fake_ollama.vision_system_prompts == [
        "global segment system prompt",
        "global segment system prompt",
    ]
    assert "global after segment 1" in fake_ollama.vision_prompts[1]
    assert fake_ollama.summary_model == "summary-model"
    assert fake_ollama.summary_system_prompt == "global final system prompt"
    assert fake_ollama.summary_prompt is not None
    assert "segment 1 summary" in fake_ollama.summary_prompt
    assert "segment 2 summary" in fake_ollama.summary_prompt
    assert "updated_global_summary" not in fake_ollama.summary_prompt
    assert "updated_timeline" not in fake_ollama.summary_prompt
    assert summary.model_used == "summary-model"
    assert summary.short_summary == "final video summary"
    assert len(segments) == 2
