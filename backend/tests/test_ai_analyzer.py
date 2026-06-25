from app.models.db_models import VideoSegmentSummary
from app.services.ai_analyzer import (
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


def test_normalize_video_segment_analysis_fills_missing_global_summary() -> None:
    normalized = _normalize_video_segment_analysis(
        {
            "current_segment_summary": "当前片段显示桌面操作",
            "current_segment_tags": "桌面",
            "confidence": 1.4,
        },
        previous_global_summary="前一段内容",
        previous_timeline=[{"start_time": "00:00:00", "end_time": "00:00:05", "summary": "开场"}],
    )
    assert normalized["current_segment_tags"] == ["桌面"]
    assert normalized["updated_global_summary"] == "前一段内容\n当前片段显示桌面操作"
    assert normalized["updated_timeline"] == [{"start_time": "00:00:00", "end_time": "00:00:05", "summary": "开场"}]
    assert normalized["confidence"] == 1.0


def test_normalize_video_final_summary_falls_back_to_segment_content() -> None:
    segments = [
        VideoSegmentSummary(
            segment_index=1,
            current_segment_summary="first segment shows a desk",
            current_segment_tags=["desk"],
            important_objects=["book"],
            ocr_text=["hello"],
            new_objects_or_scenes=["office"],
            confidence=0.9,
        ),
        VideoSegmentSummary(
            segment_index=2,
            current_segment_summary="second segment shows a screen",
            current_segment_tags=["screen"],
            important_objects=["monitor"],
            ocr_text=["world"],
            new_objects_or_scenes=["computer"],
            confidence=0.8,
        ),
    ]

    normalized = _normalize_video_final_summary(
        {"title": "", "confidence": "unknown"},
        segments,
        rolling_global_summary="rolling video summary",
        rolling_timeline=[{"start_time": "00:00:00", "end_time": "00:00:05", "summary": "first"}],
    )

    assert normalized["short_summary"] == "rolling video summary"
    assert normalized["objects"] == ["book", "monitor"]
    assert normalized["actions"] == ["desk", "screen"]
    assert normalized["text_visible"] == ["hello", "world"]
    assert normalized["confidence"] == "high"
