from __future__ import annotations

import uuid

from app.services.conversational_search_service import (
    MediaCandidate,
    _merge_candidates,
    _validated_blocks,
)


def _candidate(media_id: uuid.UUID, *, score: float) -> MediaCandidate:
    return MediaCandidate(
        media_id=media_id,
        path=f"f:/photos/{media_id}.jpg",
        media_type="image",
        captured_at=None,
        parent_dir="f:/photos",
        title=f"照片 {media_id}",
        short_summary="一张候选照片",
        searchable_text="候选照片描述",
        score=score,
        reason="初始召回",
    )


def test_validated_blocks_uses_ai_media_order_and_filters_invalid_ids() -> None:
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    candidates = [_candidate(first_id, score=0.4), _candidate(second_id, score=0.8)]
    raw = {
        "answer": "按你的要求筛选如下。",
        "blocks": [
            {"type": "text", "text": "先看第二张。"},
            {
                "type": "media_grid",
                "title": "最终选择",
                "items": [
                    {"media_id": str(second_id), "score": 0.7, "reason": "更适合做封面"},
                    {"media_id": str(uuid.uuid4()), "score": 1.0, "reason": "不存在"},
                    {"media_id": str(first_id), "score": 1.8, "reason": "备选"},
                    {"media_id": str(second_id), "score": 0.2, "reason": "重复"},
                ],
            },
        ],
    }

    blocks = _validated_blocks(raw, candidates)

    assert blocks[0] == {"type": "text", "text": "先看第二张。"}
    media_items = blocks[1]["items"]
    assert [item["media_id"] for item in media_items] == [str(second_id), str(first_id)]
    assert media_items[0]["match_reason"] == "更适合做封面"
    assert media_items[1]["score"] == 1.0


def test_merge_candidates_keeps_existing_order_but_uses_better_score() -> None:
    media_id = uuid.uuid4()
    existing = [_candidate(media_id, score=0.2)]
    incoming = [_candidate(media_id, score=0.9)]

    merged = _merge_candidates(existing, incoming)

    assert len(merged) == 1
    assert merged[0].media_id == media_id
    assert merged[0].score == 0.9


def test_validated_blocks_limits_display_media_across_blocks() -> None:
    media_ids = [uuid.uuid4() for _ in range(35)]
    candidates = [_candidate(media_id, score=0.5) for media_id in media_ids]
    raw = {
        "answer": "很多候选。",
        "blocks": [
            {
                "type": "media_grid",
                "items": [
                    {"media_id": str(media_id), "score": 0.5, "reason": "第一组"}
                    for media_id in media_ids[:20]
                ],
            },
            {
                "type": "media_grid",
                "items": [
                    {"media_id": str(media_id), "score": 0.5, "reason": "第二组"}
                    for media_id in media_ids[20:]
                ],
            },
        ],
    }

    blocks = _validated_blocks(raw, candidates, display_limit=30)

    assert sum(len(block["items"]) for block in blocks if block["type"] == "media_grid") == 30
