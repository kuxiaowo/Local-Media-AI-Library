from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm import selectinload

from app.database import SessionLocal, get_db
from app.models.db_models import SearchConversation, SearchMessage
from app.models.schemas import (
    ChatStreamRequest,
    SearchConversationRead,
    SearchConversationSummaryRead,
    SearchRequest,
    SearchResponse,
)
from app.services.conversational_search_service import run_agent_turn_events
from app.services.ollama_client import OllamaClient
from app.services.search_service import search_media

router = APIRouter(prefix="/search", tags=["search"])


@router.post("", response_model=SearchResponse)
async def search(payload: SearchRequest, db: Session = Depends(get_db)) -> SearchResponse:
    return await search_media(db, payload, OllamaClient())


@router.get("/conversations", response_model=list[SearchConversationSummaryRead])
def list_search_conversations(db: Session = Depends(get_db)) -> list[SearchConversation]:
    return list(
        db.scalars(
            select(SearchConversation)
            .order_by(SearchConversation.last_message_at.desc())
            .limit(50)
        ).all()
    )


@router.get("/conversations/{conversation_id}", response_model=SearchConversationRead)
def get_search_conversation(conversation_id: uuid.UUID, db: Session = Depends(get_db)) -> SearchConversation:
    conversation = db.scalar(
        select(SearchConversation)
        .options(selectinload(SearchConversation.messages))
        .where(SearchConversation.id == conversation_id)
    )
    if conversation is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.post("/chat/stream")
async def stream_search_chat(payload: ChatStreamRequest) -> StreamingResponse:
    return StreamingResponse(
        _chat_event_stream(payload),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _chat_event_stream(payload: ChatStreamRequest):
    tool_events: list[dict[str, Any]] = []
    assistant_saved = False
    with SessionLocal() as db:
        try:
            conversation = _get_or_create_conversation(db, payload)
            user_message = SearchMessage(
                conversation_id=conversation.id,
                role="user",
                content=payload.message.strip(),
                blocks=[{"type": "text", "text": payload.message.strip()}],
                tool_events=[],
            )
            now = datetime.now(timezone.utc)
            conversation.last_message_at = now
            if not conversation.title:
                conversation.title = _conversation_title(payload.message)
            db.add_all([conversation, user_message])
            db.commit()
            db.refresh(user_message)

            yield _sse("conversation", {"conversation_id": str(conversation.id)})
            yield _sse("user_message", {"message": _message_payload(user_message)})

            history = list(
                db.scalars(
                    select(SearchMessage)
                    .where(SearchMessage.conversation_id == conversation.id)
                    .order_by(SearchMessage.created_at)
                ).all()
            )
            async for event in run_agent_turn_events(db, payload, history, OllamaClient()):
                if event.event in {"tool_call", "tool_result"}:
                    tool_events.append({"event": event.event, **event.data})

                if event.event == "assistant_message":
                    assistant = SearchMessage(
                        conversation_id=conversation.id,
                        role="assistant",
                        content=str(event.data.get("content") or ""),
                        blocks=event.data.get("blocks") or [],
                        tool_events=tool_events,
                    )
                    conversation.last_message_at = datetime.now(timezone.utc)
                    db.add_all([conversation, assistant])
                    db.commit()
                    db.refresh(assistant)
                    assistant_saved = True
                    yield _sse("done", {"message": _message_payload(assistant)})
                    continue

                yield _sse(event.event, event.data)
        except Exception as exc:
            db.rollback()
            if not assistant_saved:
                try:
                    conversation = _get_or_create_conversation(db, payload)
                    assistant = SearchMessage(
                        conversation_id=conversation.id,
                        role="assistant",
                        content=f"检索失败：{exc}",
                        blocks=[{"type": "text", "text": f"检索失败：{exc}"}],
                        tool_events=tool_events,
                        error_message=str(exc),
                    )
                    conversation.last_message_at = datetime.now(timezone.utc)
                    db.add_all([conversation, assistant])
                    db.commit()
                except Exception:
                    db.rollback()
            yield _sse("error", {"message": str(exc)})


def _get_or_create_conversation(db: Session, payload: ChatStreamRequest) -> SearchConversation:
    if payload.conversation_id is not None:
        conversation = db.get(SearchConversation, payload.conversation_id)
        if conversation is None:
            raise RuntimeError("Conversation not found")
        return conversation
    conversation = SearchConversation(title=_conversation_title(payload.message))
    db.add(conversation)
    db.flush()
    return conversation


def _conversation_title(message: str) -> str:
    compact = " ".join(message.split())
    if len(compact) <= 42:
        return compact or "新对话"
    return compact[:39].rstrip() + "..."


def _message_payload(message: SearchMessage) -> dict[str, Any]:
    return {
        "id": str(message.id),
        "conversation_id": str(message.conversation_id),
        "role": message.role,
        "content": message.content,
        "blocks": message.blocks or [],
        "tool_events": message.tool_events or [],
        "error_message": message.error_message,
        "created_at": message.created_at.isoformat(),
        "updated_at": message.updated_at.isoformat(),
    }


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
