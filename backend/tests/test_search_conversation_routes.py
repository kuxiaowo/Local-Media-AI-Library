from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.api.routes_search import delete_search_conversation
from app.database import Base
from app.models.db_models import SearchConversation, SearchMessage


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
