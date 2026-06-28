from __future__ import annotations

import json
from typing import Any

from app.models.schemas import ChatStreamRequest
from app.services.agent.types import AgentPlan, QueryExpansion
from app.services.agent.utils import clean_text
from app.services.ollama_client import OllamaClient


QUERY_EXPANSION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "positive_queries": {"type": "array", "items": {"type": "string"}},
        "negative_queries": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["positive_queries", "negative_queries"],
}

QUERY_EXPANDER_SYSTEM_PROMPT = """你是媒体检索 Query Expander。
你只输出 JSON，不回答用户。
positive_queries 要把抽象需求转换为可能出现在媒体摘要里的具体视觉元素、场景、物体、动作、风格和用途。
negative_queries 要包含用户明确排除或不希望出现的条件。"""


async def expand_queries(
    *,
    ollama: OllamaClient,
    model_name: str,
    request: ChatStreamRequest,
    plan: AgentPlan,
) -> QueryExpansion:
    if not plan.needs_media_evidence or plan.task_type not in {"find", "recommend", "filter", "question_answer", "refine"}:
        return QueryExpansion()
    prompt = f"""用户原始请求：
{request.message}

计划：
{json.dumps(plan.to_payload(), ensure_ascii=False, default=str)}

请输出 positive_queries 和 negative_queries。"""
    try:
        raw = await ollama.generate_text_json(
            model=model_name,
            prompt=prompt,
            schema=QUERY_EXPANSION_SCHEMA,
            system_prompt=QUERY_EXPANDER_SYSTEM_PROMPT,
        )
        return _normalize(raw, request, plan)
    except Exception:
        return fallback_expansion(request, plan)


def fallback_expansion(request: ChatStreamRequest, plan: AgentPlan) -> QueryExpansion:
    positives = [plan.semantic_query or request.message]
    text = request.message
    if "科技" in text:
        positives.extend(["电脑 屏幕 键盘 电子设备", "蓝色灯光 科技感 现代桌面", "构图干净 主体明确 适合叠加标题"])
    if "封面" in text:
        positives.extend(["适合封面 主体清晰 留白", "画面清晰 构图稳定"])
    if "电脑桌面" in text:
        positives.extend(["电脑桌面 屏幕 键盘 鼠标 桌面场景"])
    negatives = list(plan.negative_requirements)
    if "封面" in text:
        negatives.extend(["画面杂乱", "模糊", "低质量", "人太多"])
    return QueryExpansion(positive_queries=_dedupe(positives), negative_queries=_dedupe(negatives))


def _normalize(raw: dict[str, Any], request: ChatStreamRequest, plan: AgentPlan) -> QueryExpansion:
    fallback = fallback_expansion(request, plan)
    positives = _string_list(raw.get("positive_queries")) or fallback.positive_queries
    negatives = _string_list(raw.get("negative_queries")) or fallback.negative_queries
    return QueryExpansion(positive_queries=positives, negative_queries=negatives)


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

