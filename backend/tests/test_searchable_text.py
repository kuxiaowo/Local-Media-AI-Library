from app.services.searchable_text import build_searchable_text


def test_build_searchable_text_contains_core_fields() -> None:
    text = build_searchable_text(
        title="学校展示",
        captured_at="2025-06-01",
        media_type="image",
        parent_dir="d:/photos/school",
        short_summary="学生展示项目",
        detailed_summary="教室内有白板和展示材料",
        scene="classroom",
        objects=["whiteboard"],
        people=["several people"],
        actions=["presentation"],
        text_visible=["CAS"],
        location_guess="school",
        mood="focused",
        search_keywords=["学校", "展示"],
    )
    assert "学校展示" in text
    assert "whiteboard" in text
    assert "CAS" in text
