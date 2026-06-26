from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.db_models import DirectoryRule, MediaAiSummary, MediaFile
from app.services.embedding_service import generate_embedding


class FakeEmbeddingOllama:
    async def embed_text(self, *, model: str, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]


def test_generate_embedding_marks_media_done(tmp_path: Path) -> None:
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
            video_frame_strategy="hybrid",
            frame_interval_seconds=5,
            max_frames_per_video=12,
            video_frame_max_width=1280,
            video_batch_size=6,
            video_batch_overlap=1,
            analysis_detail="normal",
            enabled=True,
        )
        media = MediaFile(
            path=str(tmp_path / "image.jpg"),
            normalized_path=str(tmp_path / "image.jpg").lower(),
            root_path=str(tmp_path).lower(),
            parent_dir=str(tmp_path),
            media_type="image",
            status="embedding_pending",
            error_message="previous error",
            folder_rule=rule,
        )
        db.add_all([rule, media])
        db.flush()
        db.add(
            MediaAiSummary(
                media_id=media.id,
                model_used="vision-model",
                title="title",
                short_summary="summary",
                detailed_summary="details",
                objects=[],
                people=[],
                actions=[],
                text_visible=[],
                search_keywords=[],
                searchable_text="searchable text",
                raw_json={},
            )
        )
        db.commit()
        db.refresh(media)

        asyncio.run(generate_embedding(db, media, FakeEmbeddingOllama()))
        db.refresh(media)

    assert media.status == "done"
    assert media.error_message is None
