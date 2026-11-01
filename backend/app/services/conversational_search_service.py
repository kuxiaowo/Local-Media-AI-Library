from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.orm import Session

from app.models.db_models import SearchMessage
from app.models.schemas import ChatStreamRequest
from app.services.agent.media_library_agent import run_media_library_agent_turn_events
from app.services.agent.response_composer import _validated_blocks
from app.services.agent.retrieval import _merge_candidates
from app.services.agent.types import AgentEvent, MediaCandidate
from app.services.ollama_client import OllamaClient


async def run_agent_turn_events(
    db: Session,
    request: ChatStreamRequest,
    history: list[SearchMessage],
    ollama: OllamaClient,
) -> AsyncIterator[AgentEvent]:
    async for event in run_media_library_agent_turn_events(db, request, history, ollama):
        yield event


__all__ = [
    "AgentEvent",
    "MediaCandidate",
    "_merge_candidates",
    "_validated_blocks",
    "run_agent_turn_events",
]
