from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.db_models import EmbeddingProfile, MediaEmbedding, MediaFile
from app.services.ollama_client import OllamaClient


async def generate_embedding(db: Session, media: MediaFile, ollama: OllamaClient) -> MediaEmbedding:
    settings = get_settings()
    model_name = settings.default_embedding_model
    if media.ai_summary is None:
        raise RuntimeError("Media has no AI summary to embed")
    if media.folder_rule is None:
        raise RuntimeError("Media has no resolved directory rule")

    vector = await ollama.embed_text(
        model=model_name,
        text=media.ai_summary.searchable_text,
    )
    if not vector:
        raise RuntimeError("Ollama returned an empty embedding")

    profile = db.scalar(
        select(EmbeddingProfile).where(EmbeddingProfile.model_name == model_name)
    )
    if profile is None:
        profile = EmbeddingProfile(
            model_name=model_name,
            dimension=len(vector),
        )
        db.add(profile)
        db.flush()
    elif profile.dimension != len(vector):
        raise RuntimeError(
            f"Embedding dimension mismatch for {profile.model_name}: "
            f"expected {profile.dimension}, got {len(vector)}"
        )

    existing = db.scalar(
        select(MediaEmbedding).where(
            MediaEmbedding.media_id == media.id,
            MediaEmbedding.profile_id == profile.id,
        )
    )
    if existing is None:
        existing = MediaEmbedding(media_id=media.id, profile_id=profile.id, embedding=vector)
    existing.embedding = vector
    existing.embedded_text = media.ai_summary.searchable_text
    db.add(existing)
    db.commit()
    db.refresh(existing)
    return existing
