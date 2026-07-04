from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_search
from app.api.routes_search import delete_search_conversation
from app.database import Base
from app.models.db_models import SearchConversation, SearchMessage
from app.models.schemas import ChatStreamRequest
from app.services.agent.types import AgentEvent


def test_delete_search_conversation_removes_messages() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        conversation = SearchConversation(title="old chat")
        db.add(conversation)
        db.flush()
        db.add(
            SearchMessage(
                conversation_id=conversation.id,
                role="user",
                content="hello",
                blocks=[{"type": "text", "text": "hello"}],
                tool_events=[],
            )
        )
        db.commit()
        conversation_id = conversation.id

        response = delete_search_conversation(conversation_id, db=db)
        remaining_conversations = db.scalar(
            select(func.count(SearchConversation.id)).where(SearchConversation.id == conversation_id)
        )
        remaining_messages = db.scalar(
            select(func.count(SearchMessage.id)).where(SearchMessage.conversation_id == conversation_id)
        )

    assert response.status_code == 204
    assert remaining_conversations == 0
    assert remaining_messages == 0


def test_delete_search_conversation_returns_404_for_missing_conversation() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            delete_search_conversation(uuid.uuid4(), db=db)

    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_chat_stream_saves_messages_without_holding_initial_session(monkeypatch) -> None:
    engine = create_engine(
        "sqlite+pysqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    observed_agent_session_states: list[bool] = []

    async def fake_run_agent_turn_events(db, payload, history, ollama):
        observed_agent_session_states.append(db.in_transaction())
        assert payload.message == "hello"
        assert [message.role for message in history] == ["user"]
        yield AgentEvent("final_answer", {"final_answer": {"answer": "ok"}})
        yield AgentEvent(
            "assistant_message",
            {
                "content": "ok",
                "blocks": [{"type": "text", "text": "ok"}],
            },
        )

    monkeypatch.setattr(routes_search, "SessionLocal", SessionLocal)
    monkeypatch.setattr(routes_search, "run_agent_turn_events", fake_run_agent_turn_events)

    chunks = [
        chunk
        async for chunk in routes_search._chat_event_stream(ChatStreamRequest(message="hello"))
    ]

    with SessionLocal() as db:
        messages = list(db.scalars(select(SearchMessage).order_by(SearchMessage.created_at)).all())

    assert observed_agent_session_states == [False]
    assert any("event: done" in chunk for chunk in chunks)
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[-1].content == "ok"
