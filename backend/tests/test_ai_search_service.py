from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.services.ai_search_service import (
    AiCandidate,
    _pack_candidate_chunks,
    _parse_datetime,
    _validated_result_items,
)


def test_parse_datetime_expands_date_to_end_of_day() -> None:
    parsed = _parse_datetime("2026-06-01", end_of_day=True)

    assert parsed is not None
    assert parsed.hour == 23
    assert parsed.minute == 59


def test_validated_result_items_filters_unknown_ids_and_clamps_score() -> None:
    media_id = uuid.uuid4()
    candidate = AiCandidate(
        media=SimpleNamespace(
            id=media_id,
            path="f:/photos/a.jpg",
            media_type="image",
            captured_at=None,
        ),
        summary=SimpleNamespace(
            title="教室展示",
            short_summary="学生在教室展示项目",
        ),
    )
    raw = {
        "answer": "找到一张照片。",
        "results": [
            {"media_id": str(media_id), "score": 1.7, "reason": "符合教室展示"},
            {"media_id": str(uuid.uuid4()), "score": 0.9, "reason": "不存在"},
            {"media_id": str(media_id), "score": 0.2, "reason": "重复"},
        ],
    }

    results = _validated_result_items(raw, [candidate], limit=10)

    assert len(results) == 1
    assert results[0].media_id == media_id
    assert results[0].score == 1.0
    assert results[0].match_reason == "符合教室展示"


def test_pack_candidate_chunks_keeps_all_candidates() -> None:
    candidates = [
        AiCandidate(
            media=SimpleNamespace(
                id=uuid.uuid4(),
                path=f"f:/photos/{index}.jpg",
                media_type="image",
                captured_at=None,
                parent_dir="f:/photos",
            ),
            summary=SimpleNamespace(
                title=f"照片 {index}",
                short_summary="",
                searchable_text="学校展示 " * 40,
            ),
        )
        for index in range(5)
    ]

    chunks = _pack_candidate_chunks(candidates, max_chars=500)

    assert sum(len(chunk) for chunk in chunks) == len(candidates)
    assert len(chunks) > 1
