from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from typing import Any

from app.models.schemas import ChatStreamRequest
from app.services.agent.types import AgentContextPack, AgentPlan
from app.services.agent.utils import clamp_score, clean_text, parse_datetime, recent_range
from app.services.ollama_client import OllamaClient


TASK_TYPES = {"find", "recommend", "summarize", "question_answer", "compare", "filter", "explain", "refine"}
OUTPUT_MODES = {"media_grid", "summary", "question_answer", "text_answer", "mixed", "clarification"}
SCOPE_REFERENCES = {
    "global",
    "current_directory",
    "selected_media",
    "visible_media",
    "previous_results",
    "explicit_directory",
    "explicit_time_range",
}

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_type": {"type": "string", "enum": sorted(TASK_TYPES)},
        "output_mode": {"type": "string", "enum": sorted(OUTPUT_MODES)},
        "needs_media_evidence": {"type": "boolean"},
        "needs_visual_reinspection": {"type": "boolean"},
        "scope_reference": {"type": "string", "enum": sorted(SCOPE_REFERENCES)},
        "media_type": {"type": "string", "enum": ["image", "video", "any"]},
        "positive_requirements": {"type": "array", "items": {"type": "string"}},
        "negative_requirements": {"type": "array", "items": {"type": "string"}},
        "date_from": {"type": ["string", "null"]},
        "date_to": {"type": ["string", "null"]},
        "directory_hint": {"type": ["string", "null"]},
        "semantic_query": {"type": "string"},
        "clarification_question": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
    },
    "required": [
        "task_type",
        "output_mode",
        "needs_media_evidence",
        "needs_visual_reinspection",
        "scope_reference",
        "media_type",
        "positive_requirements",
        "negative_requirements",
        "date_from",
        "date_to",
        "directory_hint",
        "semantic_query",
        "clarification_question",
        "confidence",
    ],
}

PLANNER_SYSTEM_PROMPT = """你是本地媒体库 Agent 的 Request Planner。
你不能搜索媒体，不能回答用户，只能输出结构化 JSON 计划。
规则：
- “这个文件夹/这里”优先指 ui_context.current_directory_path。
- “这几张/选中的”优先指 ui_context.selected_media_ids。
- “这些/当前看到的”优先指 ui_context.visible_media_ids。
- “刚才那些/上面那些”优先指 conversation_context.last_shown_media_ids。
- “最近”默认指 runtime_context.today 往前 recent_default_days 天。
- “今天/昨天/上周/今年”必须根据 runtime_context.timezone 和 today 解释。
- 总结类请求 output_mode 应为 summary。
- 找图、推荐、筛选类请求通常 output_mode 为 media_grid 或 mixed。
- 问答类请求通常 output_mode 为 question_answer 或 text_answer。
- 系统能力解释、为什么搜不到、用法说明通常 needs_media_evidence=false。
- 信息不足且无法从上下文推断时 output_mode=clarification，并给 clarification_question。
只返回符合 schema 的 JSON。"""


async def plan_request(
    *,
    ollama: OllamaClient,
    model_name: str,
    request: ChatStreamRequest,
    context_pack: AgentContextPack,
) -> AgentPlan:
    prompt = f"""用户输入：
{request.message}

Context Pack：
{json.dumps(context_pack.planner_payload(), ensure_ascii=False, default=str)}

请只规划任务，不要回答用户。"""
    try:
        raw = await ollama.generate_text_json(
            model=model_name,
            prompt=prompt,
            schema=PLAN_SCHEMA,
            system_prompt=PLANNER_SYSTEM_PROMPT,
        )
        return normalize_plan(raw, request, context_pack)
    except Exception:
        return fallback_plan(request, context_pack)


def normalize_plan(raw: dict[str, Any], request: ChatStreamRequest, context_pack: AgentContextPack) -> AgentPlan:
    fallback = fallback_plan(request, context_pack)
    task_type = clean_text(raw.get("task_type")) or fallback.task_type
    if task_type not in TASK_TYPES:
        task_type = fallback.task_type
    output_mode = clean_text(raw.get("output_mode")) or fallback.output_mode
    if output_mode not in OUTPUT_MODES:
        output_mode = fallback.output_mode
    scope_reference = clean_text(raw.get("scope_reference")) or fallback.scope_reference
    if scope_reference not in SCOPE_REFERENCES:
        scope_reference = fallback.scope_reference
    media_type = clean_text(raw.get("media_type")) or fallback.media_type
    if media_type not in {"image", "video", "any"}:
        media_type = fallback.media_type
    return AgentPlan(
        task_type=task_type,
        output_mode=output_mode,
        needs_media_evidence=bool(raw.get("needs_media_evidence", fallback.needs_media_evidence)),
        needs_visual_reinspection=bool(raw.get("needs_visual_reinspection", fallback.needs_visual_reinspection)),
        scope_reference=scope_reference,
        media_type=media_type,
        positive_requirements=_string_list(raw.get("positive_requirements")) or fallback.positive_requirements,
        negative_requirements=_string_list(raw.get("negative_requirements")) or fallback.negative_requirements,
        date_from=parse_datetime(raw.get("date_from"), end_of_day=False) or fallback.date_from,
        date_to=parse_datetime(raw.get("date_to"), end_of_day=True) or fallback.date_to,
        directory_hint=clean_text(raw.get("directory_hint")) or fallback.directory_hint,
        semantic_query=clean_text(raw.get("semantic_query")) or fallback.semantic_query,
        clarification_question=clean_text(raw.get("clarification_question")) or fallback.clarification_question,
        confidence=clamp_score(raw.get("confidence"), fallback.confidence),
    )


def fallback_plan(request: ChatStreamRequest, context_pack: AgentContextPack) -> AgentPlan:
    message = request.message.strip()
    compact = message.lower()
    media_type = _infer_media_type(message, request.media_type)
    task_type = "find"
    output_mode = "media_grid"
    needs_media_evidence = True
    confidence = 0.55

    if _matches(message, r"为什么.*搜不到|搜不到.*为什么|怎么搜|如何搜|系统.*能力|能不能|怎么用|解释"):
        task_type = "explain"
        output_mode = "text_answer"
        needs_media_evidence = False
        confidence = 0.75
    elif _matches(message, r"总结|概括|汇总|主要.*内容|这个文件夹.*什么|文件夹.*内容"):
        task_type = "summarize"
        output_mode = "summary"
        confidence = 0.75
    elif _matches(message, r"刚才|上面|那些|继续|筛掉|排除|不要|去掉|更暗|太暗|太亮"):
        task_type = "refine"
        output_mode = "media_grid"
        confidence = 0.7
    elif _matches(message, r"有没有|是否|是不是|大部分|多少|几.*个|为什么|吗[？?]?$"):
        task_type = "question_answer"
        output_mode = "question_answer"
        confidence = 0.72
    elif _matches(message, r"推荐|适合|哪张|哪一张|最好|封面|背景图|构图|清晰|模糊|闭眼"):
        task_type = "recommend"
        output_mode = "mixed"
        confidence = 0.72
    elif _matches(message, r"筛选|过滤|只要|找出|找几张|找.*照片|找.*图片|找.*视频"):
        task_type = "find"
        output_mode = "media_grid"
        confidence = 0.68

    scope_reference = _infer_scope_reference(message, context_pack)
    date_from, date_to = _infer_dates(message, context_pack)
    if date_from or date_to:
        scope_reference = "explicit_time_range" if scope_reference == "global" else scope_reference

    needs_visual_reinspection = _matches(message, r"封面|构图|闭眼|模糊|清晰|文字遮挡|背景图|好看|质量")
    negative_requirements = _negative_requirements(message)
    positive_requirements = _positive_requirements(message, task_type)
    directory_hint = _directory_hint(message, context_pack)

    if output_mode == "clarification":
        needs_media_evidence = False

    return AgentPlan(
        task_type=task_type,
        output_mode=output_mode,
        needs_media_evidence=needs_media_evidence,
        needs_visual_reinspection=needs_visual_reinspection,
        scope_reference=scope_reference,
        media_type=media_type,
        positive_requirements=positive_requirements,
        negative_requirements=negative_requirements,
        date_from=date_from,
        date_to=date_to,
        directory_hint=directory_hint,
        semantic_query=message,
        clarification_question=None,
        confidence=confidence,
    )


def _infer_media_type(message: str, request_media_type: str) -> str:
    if request_media_type in {"image", "video"}:
        return request_media_type
    if _matches(message, r"视频|录像|影片|短片|片段"):
        return "video"
    if _matches(message, r"照片|图片|图像|相片|封面图|背景图"):
        return "image"
    return "any"


def _infer_scope_reference(message: str, context_pack: AgentContextPack) -> str:
    if _matches(message, r"全库|所有媒体|全部媒体|不要限制|不限制当前目录"):
        return "global"
    if _matches(message, r"这几张|选中|我选的") and context_pack.ui_context.selected_media_ids:
        return "selected_media"
    if _matches(message, r"这些|当前看到|这一页|可见") and context_pack.ui_context.visible_media_ids:
        return "visible_media"
    if _matches(message, r"刚才|上面|那些|上一轮") and context_pack.conversation_context.last_shown_media_ids:
        return "previous_results"
    if _matches(message, r"这个文件夹|当前文件夹|这里|本目录") and context_pack.ui_context.current_directory_path:
        return "current_directory"
    if _matches(message, r"文件夹|目录|相册"):
        return "explicit_directory"
    return "global"


def _infer_dates(message: str, context_pack: AgentContextPack) -> tuple[datetime | None, datetime | None]:
    runtime = context_pack.runtime_context
    if "最近" in message:
        return recent_range(runtime.today, runtime.recent_default_days)
    try:
        today_date = date.fromisoformat(runtime.today)
    except ValueError:
        return None, None
    if "今天" in message:
        return datetime.combine(today_date, time.min), datetime.combine(today_date, time.max)
    if "昨天" in message:
        yesterday = today_date - timedelta(days=1)
        return datetime.combine(yesterday, time.min), datetime.combine(yesterday, time.max)
    if "上周" in message:
        start_this_week = today_date - timedelta(days=today_date.weekday())
        start_last_week = start_this_week - timedelta(days=7)
        end_last_week = start_this_week - timedelta(days=1)
        return datetime.combine(start_last_week, time.min), datetime.combine(end_last_week, time.max)
    if "今年" in message:
        return datetime(today_date.year, 1, 1), datetime(today_date.year, 12, 31, 23, 59, 59, 999999)
    return None, None


def _positive_requirements(message: str, task_type: str) -> list[str]:
    requirements = [message]
    if task_type == "recommend":
        if "封面" in message:
            requirements.extend(["构图干净", "主体明确", "适合叠加标题", "画面清晰"])
        if "科技" in message:
            requirements.extend(["电子设备", "屏幕", "桌面场景", "现代感"])
    return _dedupe(requirements)


def _negative_requirements(message: str) -> list[str]:
    negatives: list[str] = []
    for pattern in [r"不要([^，。,.!?？]+)", r"排除([^，。,.!?？]+)", r"筛掉([^，。,.!?？]+)", r"去掉([^，。,.!?？]+)"]:
        negatives.extend(match.group(1).strip() for match in re.finditer(pattern, message))
    if "不要太暗" in message or "太暗" in message:
        negatives.append("画面太暗")
    if "不要模糊" in message:
        negatives.append("模糊")
    return _dedupe(negatives)


def _directory_hint(message: str, context_pack: AgentContextPack) -> str | None:
    if _matches(message, r"这个文件夹|当前文件夹|这里|本目录"):
        return context_pack.ui_context.current_directory_path
    for item in context_pack.library_context.folder_tree_summary:
        path = clean_text(item.get("path"))
        name = clean_text(item.get("name"))
        if name and name in message:
            return path or name
    return None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return _dedupe(clean_text(item) for item in value)


def _dedupe(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean_text(value)
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _matches(text: str, pattern: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE) is not None

