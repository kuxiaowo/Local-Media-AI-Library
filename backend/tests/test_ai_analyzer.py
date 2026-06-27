from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.db_models import DirectoryRule, MediaFile, VideoSegmentSummary
from app.services.ai_analyzer import (
    _effective_background_context,
    _normalize_image_analysis,
    _normalize_video_final_summary,
    _normalize_video_segment_analysis,
    _normalize_video_summary,
)


def test_normalize_image_analysis_fills_missing_title() -> None:
    raw = {
        "title": "",
        "short_summary": "画面显示室内桌面上的书本和杯子。",
        "detailed_summary": "",
        "scene": "",
        "objects": "书本",
        "people": None,
        "actions": None,
        "text_visible": None,
        "search_keywords": [],
        "confidence": "unknown",
    }
    normalized = _normalize_image_analysis(raw)
    assert normalized["title"] == "画面显示室内桌面上的书本和杯子"
    assert normalized["detailed_summary"] == "画面显示室内桌面上的书本和杯子。"
    assert normalized["scene"] == "未知场景"
    assert normalized["objects"] == ["书本"]
    assert normalized["confidence"] == "medium"


def test_normalize_video_summary_fills_required_fields() -> None:
    frames = [
        {
            "timestamp_seconds": 0.0,
            "caption": "室内桌面上有书本",
            "scene": "室内",
            "objects": ["书本"],
            "actions": [],
            "text_visible": [],
        }
    ]
    normalized = _normalize_video_summary({"title": "", "confidence": "unknown"}, frames)
    assert normalized["title"] == "室内桌面上有书本"
    assert normalized["short_summary"] == "室内桌面上有书本"
    assert normalized["scene"] == "室内"
    assert normalized["objects"] == ["书本"]
    assert normalized["confidence"] == "medium"


def test_normalize_video_segment_analysis_updates_global_summary() -> None:
    normalized = _normalize_video_segment_analysis(
        {
            "current_segment_summary": "当前片段显示桌面操作",
            "important_observations": "桌面上出现书本",
            "updated_global_summary": "",
            "uncertain_points": "无法确定人物身份",
            "current_segment_tags": "操作",
            "important_objects": "桌面",
            "new_objects_or_scenes": ["书本"],
            "confidence": 1.4,
            "events": [{"description": "legacy event"}],
            "ocr_text": ["legacy ocr"],
        },
        previous_global_summary="前一段内容",
    )
    assert normalized["current_segment_summary"] == "当前片段显示桌面操作"
    assert normalized["important_observations"] == ["桌面上出现书本"]
    assert normalized["updated_global_summary"] == "前一段内容 当前片段显示桌面操作"
    assert normalized["uncertain_points"] == ["无法确定人物身份"]
    assert normalized["current_segment_tags"] == ["操作"]
    assert normalized["important_objects"] == ["桌面"]
    assert normalized["new_objects_or_scenes"] == ["书本"]
    assert normalized["confidence"] == 1.0
    assert "events" not in normalized
    assert "ocr_text" not in normalized


def test_normalize_video_segment_analysis_limits_global_summary() -> None:
    normalized = _normalize_video_segment_analysis(
        {
            "current_segment_summary": "当前片段",
            "updated_global_summary": "很长" * 200,
            "confidence": 0.5,
        },
        previous_global_summary="前文",
    )
    assert len(normalized["updated_global_summary"]) <= 250


def test_normalize_video_final_summary_falls_back_to_segment_content() -> None:
    segments = [
        VideoSegmentSummary(
            segment_index=1,
            start_time_seconds=0.0,
            end_time_seconds=5.0,
            current_segment_summary="first segment shows a desk",
            important_observations=["desk appears"],
            current_segment_tags=["desk"],
            important_objects=["book"],
            new_objects_or_scenes=["office"],
            uncertain_points=[],
            confidence=0.9,
        ),
        VideoSegmentSummary(
            segment_index=2,
            start_time_seconds=5.0,
            end_time_seconds=10.0,
            current_segment_summary="second segment shows a screen",
            important_observations=["screen appears"],
            current_segment_tags=["screen"],
            important_objects=["monitor"],
            new_objects_or_scenes=["computer"],
            uncertain_points=["unclear user action"],
            confidence=0.8,
        ),
    ]

    normalized = _normalize_video_final_summary(
        {"title": "", "confidence": "unknown"},
        segments,
        final_global_summary="rolling video summary",
    )

    assert normalized["short_summary"] == "rolling video summary"
    assert normalized["detailed_summary"] == (
        "[00:00:00 - 00:00:05] first segment shows a desk\n"
        "[00:00:05 - 00:00:10] second segment shows a screen"
    )
    assert normalized["timeline"][0]["summary"] == "first segment shows a desk"
    assert normalized["objects"] == ["book", "monitor"]
    assert normalized["actions"] == ["desk", "screen"]
    assert normalized["text_visible"] == []
    assert normalized["uncertain_points"] == ["unclear user action"]
    assert normalized["confidence"] == "high"


def test_effective_background_context_joins_enabled_ancestor_rules_then_media_context() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        root_rule = _directory_rule("F:/Photos", "f:/photos", background_context="root context")
        child_rule = _directory_rule("F:/Photos/School", "f:/photos/school", background_context="school context")
        leaf_rule = _directory_rule("F:/Photos/School/Event", "f:/photos/school/event", background_context="event context")
        disabled_rule = _directory_rule(
            "F:/Photos/School/Event/Disabled",
            "f:/photos/school/event/disabled",
            background_context="disabled context",
            enabled=False,
        )
        media = MediaFile(
            path="F:/Photos/School/Event/image.jpg",
            normalized_path="f:/photos/school/event/image.jpg",
            root_path="f:/photos",
            parent_dir="f:/photos/school/event",
            media_type="image",
            status="metadata_done",
            folder_rule=leaf_rule,
            background_context="single media context",
        )
        db.add_all([root_rule, child_rule, leaf_rule, disabled_rule, media])
        db.commit()

        context = _effective_background_context(db, media)

    assert context == "root context\nschool context\nevent context\nsingle media context"


def _directory_rule(
    path: str,
    normalized_path: str,
    *,
    background_context: str | None = None,
    enabled: bool = True,
) -> DirectoryRule:
    return DirectoryRule(
        path=path,
        normalized_path=normalized_path,
        recursive=True,
        vision_model="vision-model",
        summary_model="summary-model",
        background_context=background_context,
        video_frame_strategy="hybrid",
        frame_interval_seconds=5,
        max_frames_per_video=12,
        video_frame_max_width=1280,
        video_batch_size=6,
        video_batch_overlap=1,
        analysis_detail="normal",
        enabled=enabled,
    )
