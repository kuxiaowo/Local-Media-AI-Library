from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.db_models import DirectoryRule, MediaAiSummary, MediaFile, VideoSegmentSummary
from app.models.schemas import ChatAgentContext, ChatRuntimeContext, ChatStreamRequest, ChatUiContext
from app.services.agent.context_builder import build_context_pack
from app.services.agent.evidence_builder import build_evidence
from app.services.agent import media_library_agent
from app.services.agent.media_library_agent import _list_media_descriptions
from app.services.agent.planner import fallback_plan, normalize_plan
from app.services.agent.response_composer import compose_response_blocks
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


class PagingThenAnswerOllama:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_text_json(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "action": "list_media_descriptions",
                "reason_summary": "先读取当前目录的媒体描述。",
                "arguments": {
                    "directory_path": "f:/photos/current",
                    "media_type": "image",
                    "page": 1,
                    "page_size": 40,
                    "sort": "captured_desc",
                },
                "visible_memory_update": {
                    "known_facts": [],
                    "checked_scopes": ["f:/photos/current 第1页"],
                    "candidate_media_ids": [],
                    "rejected_scopes": [],
                },
            }
        prompt = kwargs.get("prompt") or ""
        match = re.search(r'"media_id":\s*"([^"]+)"', prompt)
        media_id = match.group(1) if match else str(uuid.uuid4())
        return {
            "action": "answer_now",
            "reason_summary": "已读取足够描述，可以回答。",
            "arguments": {
                "answer_type": "media_selection",
                "answer": "找到一张电脑桌面照片。",
                "selected_media_ids": [media_id],
                "confidence": "high",
                "checked_scope_summary": "检查了当前目录第 1 页图片描述。",
                "limitations": "只基于已有摘要。",
            },
            "visible_memory_update": {
                "known_facts": ["当前目录包含电脑桌面照片"],
                "checked_scopes": ["f:/photos/current 第1页"],
                "candidate_media_ids": [media_id],
                "rejected_scopes": [],
            },
        }

    async def embed_text(self, **_kwargs):
        raise RuntimeError("no embedding in unit test")


class MisclassifiedMediaSelectionOllama(PagingThenAnswerOllama):
    async def generate_text_json(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "response_mode": "use_tool",
                "visible_response": {
                    "text": "",
                    "answer_type": "answer",
                    "confidence": "medium",
                    "checked_scope_summary": "",
                    "limitations": "",
                },
                "tool_request": {
                    "name": "list_media_descriptions",
                    "reason_summary": "先读取当前目录的媒体描述。",
                    "arguments": {
                        "directory_path": "f:/photos/current",
                        "media_type": "image",
                        "page": 1,
                        "page_size": 40,
                        "sort": "captured_desc",
                    },
                },
                "media": {"selected_media_ids": []},
                "visible_memory_update": {
                    "known_facts": [],
                    "checked_scopes": ["f:/photos/current 第 1 页"],
                    "candidate_media_ids": [],
                    "rejected_scopes": [],
                },
            }
        prompt = kwargs.get("prompt") or ""
        match = re.search(r'"media_id":\s*"([^"]+)"', prompt)
        media_id = match.group(1) if match else str(uuid.uuid4())
        return {
            "response_mode": "answer",
            "visible_response": {
                "text": "找到一张电脑桌面照片。",
                "answer_type": "summary",
                "confidence": "high",
                "checked_scope_summary": "检查了当前目录第 1 页图片描述。",
                "limitations": "只基于已有摘要。",
            },
            "media": {"selected_media_ids": [media_id]},
            "visible_memory_update": {
                "known_facts": ["当前目录包含电脑桌面照片"],
                "checked_scopes": ["f:/photos/current 第 1 页"],
                "candidate_media_ids": [media_id],
                "rejected_scopes": [],
            },
        }


class DirectAnswerForMediaSearchOllama:
    async def generate_text_json(self, **_kwargs):
        return {
            "response_mode": "answer",
            "visible_response": {
                "text": "根据目录信息可以直接回答。",
                "answer_type": "summary",
                "confidence": "high",
                "checked_scope_summary": "只看了目录信息。",
                "limitations": "没有读取媒体候选。",
            },
            "media": {"selected_media_ids": []},
            "visible_memory_update": {
                "known_facts": [],
                "checked_scopes": ["directory_context"],
                "candidate_media_ids": [],
                "rejected_scopes": [],
            },
        }

    async def embed_text(self, **_kwargs):
        raise RuntimeError("no embedding in unit test")


class DirectAnswerThenSelectReverseOllama:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_text_json(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "response_mode": "answer",
                "visible_response": {
                    "text": "根据目录信息可以直接回答。",
                    "answer_type": "summary",
                    "confidence": "high",
                    "checked_scope_summary": "只看了目录信息。",
                    "limitations": "没有读取媒体候选。",
                },
                "media": {"selected_media_ids": []},
                "visible_memory_update": {
                    "known_facts": [],
                    "checked_scopes": ["directory_context"],
                    "candidate_media_ids": [],
                    "rejected_scopes": [],
                },
            }
        prompt = kwargs.get("prompt") or ""
        ids: list[str] = []
        for media_id in re.findall(r'"media_id":\s*"([^"]+)"', prompt):
            if media_id not in ids:
                ids.append(media_id)
        selected = list(reversed(ids[:2]))
        return {
            "response_mode": "answer",
            "visible_response": {
                "text": "我从候选里挑了两个更适合的媒体。",
                "answer_type": "media_selection",
                "confidence": "high",
                "checked_scope_summary": "检查了检索候选。",
                "limitations": "只基于已有摘要。",
            },
            "media": {"selected_media_ids": selected},
            "visible_memory_update": {
                "known_facts": ["已按候选摘要挑选媒体"],
                "checked_scopes": ["search_descriptions candidates"],
                "candidate_media_ids": ids,
                "rejected_scopes": [],
            },
        }

    async def embed_text(self, **_kwargs):
        raise RuntimeError("no embedding in unit test")


class LoopingOllama:
    async def generate_text_json(self, **_kwargs):
        return {
            "action": "search_descriptions",
            "reason_summary": "继续检索夏天相关描述。",
            "arguments": {"query": "夏天", "limit": 10},
            "visible_memory_update": {
                "known_facts": [],
                "checked_scopes": ["global_search_2025_summer"],
                "candidate_media_ids": [],
                "rejected_scopes": [],
            },
        }

    async def embed_text(self, **_kwargs):
        raise AssertionError("max-turn fallback should not run vector embedding")


class DirectoryInfoThenAnswerOllama:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_text_json(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "response_mode": "use_tool",
                "visible_response": {
                    "text": "",
                    "answer_type": "summary",
                    "confidence": "medium",
                    "checked_scope_summary": "",
                    "limitations": "",
                },
                "tool_request": {
                    "name": "list_directory_info",
                    "reason_summary": "先读取当前资源目录的统计信息。",
                    "arguments": {"directory_path": "F:/Photos/Current", "page": 1, "page_size": 40},
                },
                "media": {"selected_media_ids": []},
                "visible_memory_update": {
                    "known_facts": [],
                    "checked_scopes": ["f:/photos/current 目录统计"],
                    "candidate_media_ids": [],
                    "rejected_scopes": [],
                },
            }
        prompt = kwargs.get("prompt") or ""
        assert '"directory_stats"' in prompt
        return {
            "response_mode": "answer",
            "visible_response": {
                "text": "当前资源目录下有 f:/photos/current，包含 2 个已分析媒体：1 张图片和 1 个视频。",
                "answer_type": "summary",
                "confidence": "high",
                "checked_scope_summary": "读取了 f:/photos/current 的目录统计。",
                "limitations": "目录结构来自数据库中已有 AI 摘要的媒体目录，不代表未扫描的空文件夹。",
            },
            "media": {"selected_media_ids": []},
            "visible_memory_update": {
                "known_facts": ["f:/photos/current 有 2 个已分析媒体"],
                "checked_scopes": ["f:/photos/current 目录统计"],
                "candidate_media_ids": [],
                "rejected_scopes": [],
            },
        }

    async def embed_text(self, **_kwargs):
        raise RuntimeError("directory answer should not run embedding")


def test_fallback_planner_identifies_core_task_types() -> None:
    context = _context_pack()

    assert fallback_plan(ChatStreamRequest(message="总结这个文件夹主要是什么内容"), context).task_type == "summarize"
    assert fallback_plan(ChatStreamRequest(message="找几张科技感照片"), context).task_type == "find"
    assert fallback_plan(ChatStreamRequest(message="这些照片里是不是大部分是电脑桌面"), context).task_type == "question_answer"
    assert fallback_plan(ChatStreamRequest(message="刚才那些里不要太暗的"), context).task_type == "refine"
    assert fallback_plan(ChatStreamRequest(message="为什么搜不到我想要的图"), context).task_type == "explain"


def test_planner_treats_retrospective_time_lookup_as_summary() -> None:
    context = _context_pack()

    plan = fallback_plan(ChatStreamRequest(message="查找2025年夏天发生了什么"), context)

    assert plan.task_type == "summarize"
    assert plan.output_mode == "summary"
    assert plan.needs_media_evidence is True
    assert plan.scope_reference == "explicit_time_range"
    assert plan.media_type == "any"
    assert plan.date_from == datetime(2025, 6, 1, 0, 0, 0)
    assert plan.date_to == datetime(2025, 8, 31, 23, 59, 59)
    assert plan.should_show_media_grid is False


def test_normalize_plan_overrides_model_find_for_retrospective_summary() -> None:
    context = _context_pack()
    request = ChatStreamRequest(message="查找2025年夏天发生了什么")
    raw = {
        "task_type": "find",
        "output_mode": "media_grid",
        "needs_media_evidence": True,
        "needs_visual_reinspection": False,
        "should_show_media_grid": True,
        "scope_reference": "global",
        "media_type": "image",
        "positive_requirements": ["2025年夏天"],
        "negative_requirements": [],
        "date_from": None,
        "date_to": None,
        "directory_hint": None,
        "semantic_query": "查找2025年夏天发生了什么",
        "clarification_question": None,
        "confidence": 0.9,
    }

    plan = normalize_plan(raw, request, context)

    assert plan.task_type == "summarize"
    assert plan.output_mode == "summary"
    assert plan.scope_reference == "explicit_time_range"
    assert plan.media_type == "any"
    assert plan.date_from == datetime(2025, 6, 1, 0, 0, 0)
    assert plan.date_to == datetime(2025, 8, 31, 23, 59, 59)
    assert plan.should_show_media_grid is False


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


def test_list_media_descriptions_filters_pages_and_caps_page_size() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        _seed_media(db)
        result = _list_media_descriptions(
            db,
            ChatStreamRequest(message="找照片"),
            {
                "directory_path": "F:/Photos/Current",
                "media_type": "image",
                "page": 1,
                "page_size": 99,
                "sort": "captured_desc",
            },
        )

    payload = result["result"]
    assert payload["page_size"] == 50
    assert payload["total"] == 1
    assert payload["items"][0]["media_type"] == "image"
    assert payload["items"][0]["searchable_text"] == "电脑 桌面 屏幕 键盘 清晰"
    assert result["candidate_media"][0]["media_id"] == payload["items"][0]["media_id"]


def test_list_media_descriptions_infers_explicit_summer_date_range() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        _seed_media(db, image_captured_at=datetime(2026, 1, 1), video_captured_at=datetime(2026, 1, 2))
        result = _list_media_descriptions(
            db,
            ChatStreamRequest(message="2025年夏天发生了什么"),
            {"page": 1, "page_size": 40, "sort": "captured_desc"},
        )

    assert result["result"]["total"] == 0
    assert result["candidate_media"] == []


def test_media_library_agent_reads_page_then_answers_with_final_json() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        image, _video = _seed_media(db)
        request = ChatStreamRequest(
            message="找一张电脑桌面照片",
            directory_path="F:/Photos/Current",
            context=ChatAgentContext(
                runtime_context=ChatRuntimeContext(today="2026-06-28"),
                ui_context=ChatUiContext(current_directory_path="F:/Photos/Current"),
            ),
        )
        events = asyncio.run(_collect_events(db, request, PagingThenAnswerOllama()))

    action_events = [event for event in events if event.event == "agent_action"]
    assert [event.data["action"] for event in action_events] == ["list_media_descriptions", "answer_now"]
    assert any(event.event == "tool_result" and event.data["tool"] == "list_media_descriptions" for event in events)
    final = [event for event in events if event.event == "final_answer"][-1].data["final_answer"]
    assert final["answer_type"] == "media_selection"
    assert final["selected_media_ids"] == [str(image.id)]
    assistant = [event for event in events if event.event == "assistant_message"][-1]
    block_types = [block["type"] for block in assistant.data["blocks"]]
    assert "media_grid" in block_types


def test_media_library_agent_keeps_media_cards_when_model_labels_selection_as_non_media_type() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        image, _video = _seed_media(db)
        request = ChatStreamRequest(
            message="找一张电脑桌面照片",
            directory_path="F:/Photos/Current",
            context=ChatAgentContext(
                runtime_context=ChatRuntimeContext(today="2026-06-28"),
                ui_context=ChatUiContext(current_directory_path="F:/Photos/Current"),
            ),
        )
        events = asyncio.run(_collect_events(db, request, MisclassifiedMediaSelectionOllama()))

    final = [event for event in events if event.event == "final_answer"][-1].data["final_answer"]
    assert final["answer_type"] == "media_selection"
    assert final["selected_media_ids"] == [str(image.id)]
    assistant = [event for event in events if event.event == "assistant_message"][-1]
    block_types = [block["type"] for block in assistant.data["blocks"]]
    assert "media_grid" in block_types


def test_media_library_agent_forces_search_but_does_not_vector_fill_cards() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        _seed_media(db)
        request = ChatStreamRequest(message="找一张电脑桌面照片", media_type="image", limit=5)
        events = asyncio.run(_collect_events(db, request, DirectAnswerForMediaSearchOllama()))

    action_events = [event for event in events if event.event == "agent_action"]
    assert action_events[0].data["action"] == "search_descriptions"
    assert any(event.event == "tool_result" and event.data["tool"] == "search_descriptions" for event in events)
    final = [event for event in events if event.event == "final_answer"][-1].data["final_answer"]
    assert final["answer_type"] == "summary"
    assert "selected_media_ids" not in final
    assistant = [event for event in events if event.event == "assistant_message"][-1]
    block_types = [block["type"] for block in assistant.data["blocks"]]
    assert "media_grid" not in block_types


def test_media_library_agent_uses_ai_selected_count_and_order_after_search() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        _seed_media(db)
        request = ChatStreamRequest(message="找几个电脑桌面媒体", media_type="any", limit=5)
        events = asyncio.run(_collect_events(db, request, DirectAnswerThenSelectReverseOllama()))

    tool_result = [event for event in events if event.event == "tool_result" and event.data["tool"] == "search_descriptions"][-1]
    candidate_ids = [item["media_id"] for item in tool_result.data["result"]["items"]]
    expected = list(reversed(candidate_ids[:2]))
    final = [event for event in events if event.event == "final_answer"][-1].data["final_answer"]
    assert final["answer_type"] == "media_selection"
    assert final["selected_media_ids"] == expected
    assistant = [event for event in events if event.event == "assistant_message"][-1]
    media_grid = [block for block in assistant.data["blocks"] if block["type"] == "media_grid"][0]
    assert [item["media_id"] for item in media_grid["items"]] == expected


def test_media_library_agent_directory_answer_uses_visible_response_without_media_cards() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        _seed_media(db)
        request = ChatStreamRequest(message="只需要把当前目录结构输出给我", directory_path="F:/Photos/Current")
        events = asyncio.run(_collect_events(db, request, DirectoryInfoThenAnswerOllama()))

    action_events = [event for event in events if event.event == "agent_action"]
    assert [event.data["action"] for event in action_events] == ["list_directory_info", "answer_now"]
    assert action_events[-1].data["visible_response"]["text"].startswith("当前资源目录下有")
    assert any(event.event == "tool_result" and event.data["tool"] == "list_directory_info" for event in events)
    final = [event for event in events if event.event == "final_answer"][-1].data["final_answer"]
    assert final["answer_type"] == "summary"
    assert "f:/photos/current" in final["answer"]
    assert "selected_media_ids" not in final
    assistant = [event for event in events if event.event == "assistant_message"][-1]
    block_types = [block["type"] for block in assistant.data["blocks"]]
    assert "media_grid" not in block_types


def test_media_library_agent_max_turns_does_not_vector_fallback_outside_date_range() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        _seed_media(db, image_captured_at=datetime(2026, 1, 1), video_captured_at=datetime(2026, 1, 2))
        events = asyncio.run(_collect_events(db, ChatStreamRequest(message="2025年夏天发生了什么"), LoopingOllama()))

    final = [event for event in events if event.event == "final_answer"][-1].data["final_answer"]
    assert final["answer_type"] == "answer"
    assert "selected_media_ids" not in final
    assert "2025-06-01" in final["answer"]
    assert "2025-08-31" in final["answer"]
    assert "可能原因" in final["answer"]
    assert "其他年份" in final["answer"]
    tool_result = [event for event in events if event.event == "tool_result"][-1]
    assert tool_result.data["tool"] == "fallback_no_media_selection"
    assistant = [event for event in events if event.event == "assistant_message"][-1]
    block_types = [block["type"] for block in assistant.data["blocks"]]
    assert "media_grid" not in block_types


def test_media_library_agent_uses_configured_max_turns(monkeypatch) -> None:
    monkeypatch.setattr(
        media_library_agent,
        "get_settings",
        lambda: SimpleNamespace(
            default_ai_search_model="agent-model",
            default_summary_model="summary-model",
            default_embedding_model="",
            ai_search_max_turns=2,
        ),
    )
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        _seed_media(db, image_captured_at=datetime(2026, 1, 1), video_captured_at=datetime(2026, 1, 2))
        events = asyncio.run(_collect_events(db, ChatStreamRequest(message="2025年夏天发生了什么"), LoopingOllama()))

    search_actions = [
        event for event in events if event.event == "agent_action" and event.data["action"] == "search_descriptions"
    ]
    assert len(search_actions) == 2
    final_tool = [event for event in events if event.event == "tool_result"][-1]
    assert final_tool.data["tool"] == "fallback_no_media_selection"


def test_media_library_agent_fallback_on_invalid_json_does_not_emit_vector_cards() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        image, _video = _seed_media(db)
        events = asyncio.run(_collect_events(db, ChatStreamRequest(message="电脑桌面", media_type="image"), FakeOllama()))

    action = [event for event in events if event.event == "agent_action"][-1]
    assert action.data["action"] == "fallback"
    final = [event for event in events if event.event == "final_answer"][-1].data["final_answer"]
    assert final["confidence"] == "low"
    assert "selected_media_ids" not in final
    assistant = [event for event in events if event.event == "assistant_message"][-1]
    block_types = [block["type"] for block in assistant.data["blocks"]]
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
    assert context.library_overview["media_count"] == 2
    assert context.directory_tree[0]["path"] == "f:/photos/current"
    assert context.directory_stats[0]["path"] == "f:/photos/current"
    assert context.directory_stats[0]["background_context"] == "这个目录是电脑桌面素材。"
    assert "电脑桌面" in context.directory_stats[0]["common_keywords"]


def test_response_blocks_filter_debug_stats_fields() -> None:
    blocks = compose_response_blocks(
        request=ChatStreamRequest(message="总结一下"),
        plan=AgentPlan(task_type="summarize", output_mode="summary", should_show_media_grid=False),
        scope=Scope(),
        judge_result=JudgeResult(
            summary="这是自然语言总结。",
            stats={
                "checked_count": 3,
                "matched_count": 3,
                "model_revision": "debug-model",
                "reasoning_count": 9,
                "vision_encodings_per_sec": 12.5,
            },
        ),
        candidates=[],
    )

    visible = str(blocks)
    assert "model_revision" not in visible
    assert "reasoning_count" not in visible
    assert "vision_encodings_per_sec" not in visible
    stats_blocks = [block for block in blocks if block["type"] == "stats"]
    assert stats_blocks == [{"type": "stats", "stats": {"checked_count": 3, "matched_count": 3}}]


async def _collect_events(db, request: ChatStreamRequest, ollama=None):
    return [event async for event in run_agent_turn_events(db, request, [], ollama or FakeOllama())]


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


def _seed_media(
    db,
    *,
    image_captured_at: datetime = datetime(2026, 6, 1),
    video_captured_at: datetime = datetime(2026, 6, 2),
):
    rule = DirectoryRule(
        path="F:/Photos",
        normalized_path="f:/photos",
        recursive=True,
        vision_model="vision-model",
        summary_model="summary-model",
        background_context="这个目录是电脑桌面素材。",
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
        captured_at=image_captured_at,
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
        captured_at=video_captured_at,
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
