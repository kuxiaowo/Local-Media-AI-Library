from datetime import datetime, timezone

from app.services.search_rerank import RerankInput, final_score, keyword_score, time_score


def test_keyword_score_counts_query_terms() -> None:
    assert keyword_score("学校 展示", "学校项目展示活动") == 1.0


def test_keyword_score_handles_chinese_query_without_spaces() -> None:
    assert keyword_score("海边日落", "海边的日落照片") > 0.6


def test_time_score_requires_range_match() -> None:
    captured = datetime(2025, 7, 1, tzinfo=timezone.utc)
    start = datetime(2025, 6, 1, tzinfo=timezone.utc)
    end = datetime(2025, 8, 31, tzinfo=timezone.utc)
    assert time_score(captured, start, end) == 1.0


def test_final_score_is_weighted() -> None:
    score = final_score(
        RerankInput(
            vector_score=0.8,
            query="学校",
            text="学校展示",
            captured_at=None,
            folder_match=True,
        )
    )
    assert 0.7 < score < 0.8
