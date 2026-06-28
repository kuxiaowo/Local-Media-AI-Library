from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.db_models import DirectoryRule, MediaAiSummary, MediaFile, VideoSegmentSummary
from app.models.schemas import ChatAgentContext, ChatRuntimeContext, ChatStreamRequest, ChatUiContext
from app.services.agent.context_builder import build_context_pack
from app.services.agent.evidence_builder import build_evidence
from app.services.agent.planner import fallback_plan
from app.services.agent.scope_resolver import resolve_scope
from app.services.agent.types import (
    AgentContextPack,
    AgentPlan,
    ConversationContext,
    JudgeResult,
    LibraryContext,
    LibraryRootContext,
    MediaCandidate,
    RuntimeContext,
    Scope,
    UiContext,
)
from app.services.agent.validators import validate_judge_result
from app.services.conversational_search_service import run_agent_turn_events


class FakeOllama:
    async def generate_text_json(self, **_kwargs):
        raise RuntimeError("no model in unit test")

    async def embed_text(self, **_kwargs):
        raise RuntimeError("no embedding in unit test")


def test_fallback_planner_identifies_core_task_types() -> None:
    context = _context_pack()

    assert fallback_plan(ChatStreamRequest(message="总结这个文件夹主要是什么内容"), context).task_type == "summarize"
    assert fallback_plan(ChatStreamRequest(message="找几张科技感照片"), context).task_type == "find"
    assert fallback_plan(ChatStreamRequest(message="这些照片里是不是大部分是电脑桌面"), context).task_type == "question_answer"
    assert fallback_plan(ChatStreamRequest(message="刚才那些里不要太暗的"), context).task_type == "refine"
    assert fallback_plan(ChatStreamRequest(message="为什么搜不到我想要的图"), context).task_type == "explain"


def test_resolve_scope_handles_ui_history_and_recent_range() -> None:
    selected_id = uuid.uuid4()
    visible_id = uuid.uuid4()
    previous_id = uuid.uuid4()
    context = _context_pack(
        selected=[str(selected_id)],
        visible=[str(visible_id)],
        previous=[str(previous_id)],
    )
    request = ChatStreamRequest(message="scope test", media_type="any")

    current = resolve_scope(AgentPlan(scope_reference="current_directory"), request, context)
    assert current.directory_paths == ["f:/photos/current"]

    selected = resolve_scope(AgentPlan(scope_reference="selected_media"), request, context)
    assert selected.media_ids == [selected_id]

    visible = resolve_scope(AgentPlan(scope_reference="visible_media"), request, context)
    assert visible.media_ids == [visible_id]

    previous = resolve_scope(AgentPlan(scope_reference="previous_results"), request, context)
    assert previous.media_ids == [previous_id]

    recent_plan = fallback_plan(ChatStreamRequest(message="最近的视频"), context)
    recent = resolve_scope(recent_plan, ChatStreamRequest(message="最近的视频"), context)
    assert recent.media_type == "video"
    assert recent.date_from is not None
    assert recent.date_to is not None


def test_evidence_builder_outputs_detailed_image_and_video_segments() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        image, video = _seed_media(db)
        db.add(
            VideoSegmentSummary(
                media_id=video.id,
                segment_index=0,
                start_time_seconds=0,
                end_time_seconds=5,
                current_segment_summary="电脑桌面出现在片段中",
                important_observations=["屏幕亮起"],
                current_segment_tags=["电脑"],
                important_objects=["显示器"],
                new_objects_or_scenes=["桌面"],
                updated_global_summary="视频展示电脑桌面",
                uncertain_points=[],
                confidence=0.8,
            )
        )
        db.commit()

        image_evidence = build_evidence(db, [image.id], detail_level="detailed")
        video_evidence = build_evidence(db, [video.id], detail_level="video_timeline")

    assert image_evidence[0]["detailed_summary"] == "清晰的电脑桌面照片"
    assert image_evidence[0]["objects"] == ["电脑", "键盘"]
    assert video_evidence[0]["video_segments"][0]["current_segment_summary"] == "电脑桌面出现在片段中"


def test_validator_filters_fabricated_wrong_type_out_of_scope_and_duplicates() -> None:
    good_id = uuid.uuid4()
    wrong_type_id = uuid.uuid4()
    out_dir_id = uuid.uuid4()
    candidates = [
        _candidate(good_id, media_type="image", parent_dir="f:/photos/current"),
        _candidate(wrong_type_id, media_type="video", parent_dir="f:/photos/current"),
        _candidate(out_dir_id, media_type="image", parent_dir="f:/other"),
    ]
    evidence = [
        _evidence(good_id, media_type="image", parent_dir="f:/photos/current"),
        _evidence(wrong_type_id, media_type="video", parent_dir="f:/photos/current"),
        _evidence(out_dir_id, media_type="image", parent_dir="f:/other"),
    ]
    result = JudgeResult(
        selected=[
            {"media_id": str(good_id), "score": 2, "reason": "ok", "confidence": 0.5},
            {"media_id": str(good_id), "score": 0.5, "reason": "dup", "confidence": 0.5},
            {"media_id": str(uuid.uuid4()), "score": 1, "reason": "fake", "confidence": 0.5},
            {"media_id": str(wrong_type_id), "score": 1, "reason": "wrong", "confidence": 0.5},
            {"media_id": str(out_dir_id), "score": 1, "reason": "out", "confidence": 0.5},
        ],
        stats={},
    )
    validated = validate_judge_result(
        result,
        candidates=candidates,
        evidence_items=evidence,
        plan=AgentPlan(output_mode="media_grid"),
        scope=Scope(media_type="image", directory_paths=["f:/photos/current"]),
        limit=10,
    )

    assert [item["media_id"] for item in validated.selected] == [str(good_id)]
    assert validated.selected[0]["score"] == 1.0


def test_validator_handles_mixed_timezone_awareness() -> None:
    media_id = uuid.uuid4()
    candidates = [_candidate(media_id, media_type="image", parent_dir="f:/photos/current")]
    evidence = [
        {
            **_evidence(media_id, media_type="image", parent_dir="f:/photos/current"),
            "captured_at": "2025-07-01T08:00:00+00:00",
        }
    ]
    result = JudgeResult(
        selected=[{"media_id": str(media_id), "score": 0.8, "reason": "ok", "confidence": 0.8}],
        stats={},
    )

    validated = validate_judge_result(
        result,
        candidates=candidates,
        evidence_items=evidence,
        plan=AgentPlan(output_mode="media_grid"),
        scope=Scope(date_from=datetime(2025, 7, 1), date_to=datetime(2025, 7, 2)),
        limit=10,
    )

    assert [item["media_id"] for item in validated.selected] == [str(media_id)]


def test_summarize_agent_turn_does_not_need_vector_retrieval(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        _seed_media(db)

        async def fail_retrieval(*_args, **_kwargs):
            raise AssertionError("summarize should not call broad retrieval")

        import app.services.conversational_search_service as service

        monkeypatch.setattr(service, "retrieve_broad_candidates", fail_retrieval)
        request = ChatStreamRequest(
            message="总结这个文件夹主要是什么内容",
            directory_path="F:/Photos/Current",
            context=ChatAgentContext(
                runtime_context=ChatRuntimeContext(today="2026-06-28"),
                ui_context=ChatUiContext(current_directory_path="F:/Photos/Current"),
            ),
        )
        events = asyncio.run(_collect_events(db, request))

    assistant = [event for event in events if event.event == "assistant_message"][-1]
    assert assistant.data["blocks"][0]["type"] == "summary"


def test_question_answer_turn_does_not_force_media_grid() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        image, _video = _seed_media(db)
        request = ChatStreamRequest(
            message="这些照片里是不是大部分是电脑桌面",
            context=ChatAgentContext(
                runtime_context=ChatRuntimeContext(today="2026-06-28"),
                ui_context=ChatUiContext(visible_media_ids=[image.id]),
            ),
        )
        events = asyncio.run(_collect_events(db, request))

    assistant = [event for event in events if event.event == "assistant_message"][-1]
    block_types = [block["type"] for block in assistant.data["blocks"]]
    assert "question_answer" in block_types
    assert "media_grid" not in block_types


def test_context_pack_falls_back_when_frontend_sends_no_context() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        _seed_media(db)
        context = build_context_pack(db, ChatStreamRequest(message="找照片"), [])

    assert context.runtime_context.timezone == "Asia/Shanghai"
    assert context.ui_context.page == "agent"
    assert context.library_context.roots


async def _collect_events(db, request: ChatStreamRequest):
    return [event async for event in run_agent_turn_events(db, request, [], FakeOllama())]


def _context_pack(
    *,
    selected: list[str] | None = None,
    visible: list[str] | None = None,
    previous: list[str] | None = None,
) -> AgentContextPack:
    return AgentContextPack(
        runtime_context=RuntimeContext(
            now_iso="2026-06-28T12:00:00+08:00",
            today="2026-06-28",
            timezone="Asia/Shanghai",
            locale="zh-CN",
            recent_default_days=30,
        ),
        library_context=LibraryContext(
            roots=[LibraryRootContext(path="f:/photos", display_name="Photos", enabled=True, recursive=True)],
            folder_tree_summary=[
                {"path": "f:/photos/current", "name": "current", "media_count": 2},
                {"path": "f:/photos/archive", "name": "archive", "media_count": 1},
            ],
        ),
        ui_context=UiContext(
            page="agent",
            current_directory_path="f:/photos/current",
            selected_media_ids=selected or [],
            visible_media_ids=visible or [],
            active_filters={"media_type": "any", "directory_path": None, "date_from": None, "date_to": None},
        ),
        conversation_context=ConversationContext(last_shown_media_ids=previous or []),
    )


def _seed_media(db):
    rule = DirectoryRule(
        path="F:/Photos",
        normalized_path="f:/photos",
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
    image = MediaFile(
        path="F:/Photos/Current/desk.jpg",
        normalized_path="f:/photos/current/desk.jpg",
        root_path="f:/photos",
        parent_dir="f:/photos/current",
        media_type="image",
        width=1200,
        height=800,
        captured_at=datetime(2026, 6, 1),
        status="done",
        folder_rule=rule,
    )
    video = MediaFile(
        path="F:/Photos/Current/desk.mp4",
        normalized_path="f:/photos/current/desk.mp4",
        root_path="f:/photos",
        parent_dir="f:/photos/current",
        media_type="video",
        duration_seconds=10,
        captured_at=datetime(2026, 6, 2),
        status="done",
        folder_rule=rule,
    )
    db.add_all([rule, image, video])
    db.flush()
    db.add_all(
        [
            MediaAiSummary(
                media_id=image.id,
                model_used="summary",
                title="电脑桌面",
                short_summary="一张清晰的电脑桌面照片",
                detailed_summary="清晰的电脑桌面照片",
                scene="桌面工作区",
                objects=["电脑", "键盘"],
                people=[],
                actions=[],
                text_visible=[],
                search_keywords=["电脑桌面"],
                searchable_text="电脑 桌面 屏幕 键盘 清晰",
            ),
            MediaAiSummary(
                media_id=video.id,
                model_used="summary",
                title="桌面视频",
                short_summary="电脑桌面视频",
                detailed_summary="展示电脑桌面的视频",
                scene="桌面工作区",
                objects=["电脑"],
                people=[],
                actions=[],
                text_visible=[],
                search_keywords=["电脑桌面", "视频"],
                searchable_text="电脑 桌面 视频 屏幕",
            ),
        ]
    )
    db.commit()
    return image, video


def _candidate(media_id: uuid.UUID, *, media_type: str, parent_dir: str) -> MediaCandidate:
    return MediaCandidate(
        media_id=media_id,
        path=f"{parent_dir}/{media_id}.jpg",
        media_type=media_type,
        captured_at=None,
        parent_dir=parent_dir,
        title="候选",
        short_summary="候选",
        searchable_text="候选",
        score=0.5,
        reason="候选",
        root_path="f:/photos",
    )


def _evidence(media_id: uuid.UUID, *, media_type: str, parent_dir: str) -> dict:
    return {
        "media_id": str(media_id),
        "media_type": media_type,
        "path": f"{parent_dir}/{media_id}.jpg",
        "parent_dir": parent_dir,
        "root_path": "f:/photos",
        "captured_at": None,
    }
