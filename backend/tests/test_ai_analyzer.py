from app.services.ai_analyzer import _normalize_image_analysis


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
