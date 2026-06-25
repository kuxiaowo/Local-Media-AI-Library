from __future__ import annotations

import json
from collections.abc import Iterable


VIDEO_SEGMENT_ANALYSIS_SYSTEM_PROMPT = """
你是本地媒体库的视频分段识别器。只依据当前批次关键帧描述可见内容，或由当前批次连续画面支持的内容。
previous_global_summary 和 previous_timeline 仅作上下文，不是当前批次的新证据。
所有用户可见文本使用简体中文。按 schema 返回严格 JSON，不输出解释。
""".strip()

VIDEO_SEGMENT_ANALYSIS_USER_PROMPT = """
分析当前批次关键帧，生成当前片段的结构化结果：当前片段摘要、标签、重要物体、OCR、新出现的物体或场景、置信度。
同时更新从视频开头到当前批次为止的滚动摘要和时间线。
保留用于搜索的客观线索：场景、物体、动作、可见文字和显著变化。
""".strip()

VIDEO_SEGMENT_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "current_segment_summary": {"type": "string"},
        "current_segment_tags": {"type": "array", "items": {"type": "string"}},
        "important_objects": {"type": "array", "items": {"type": "string"}},
        "ocr_text": {"type": "array", "items": {"type": "string"}},
        "new_objects_or_scenes": {"type": "array", "items": {"type": "string"}},
        "updated_global_summary": {"type": "string"},
        "updated_timeline": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_time": {"type": "string"},
                    "end_time": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["start_time", "end_time", "summary"],
            },
        },
        "confidence": {"type": "number"},
    },
    "required": [
        "current_segment_summary",
        "current_segment_tags",
        "important_objects",
        "ocr_text",
        "new_objects_or_scenes",
        "updated_global_summary",
        "updated_timeline",
        "confidence",
    ],
}

VIDEO_FINAL_SUMMARY_SYSTEM_PROMPT = """
你是本地媒体库的视频最终总结器。只基于 segments 生成视频级摘要；rolling_global_summary 和 rolling_timeline 只作整体脉络参考。
所有用户可见文本使用简体中文。按 schema 返回严格 JSON，不输出解释。
""".strip()

VIDEO_FINAL_SUMMARY_USER_PROMPT = """
整合所有 segments，生成视频级结构化摘要：标题、简短摘要、详细摘要、时间线、整体场景、重要物体、动作/事件、可见文字、搜索关键词和置信度。
保留重要可观察细节，避免重复，不要补充 segments 未支持的事件。
""".strip()

VIDEO_FINAL_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "short_summary": {"type": "string"},
        "detailed_summary": {"type": "string"},
        "timeline": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_time": {"type": "string"},
                    "end_time": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["start_time", "end_time", "summary"],
            },
        },
        "scene": {"type": "string"},
        "objects": {"type": "array", "items": {"type": "string"}},
        "actions": {"type": "array", "items": {"type": "string"}},
        "text_visible": {"type": "array", "items": {"type": "string"}},
        "search_keywords": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": [
        "title",
        "short_summary",
        "detailed_summary",
        "timeline",
        "scene",
        "objects",
        "actions",
        "text_visible",
        "search_keywords",
        "confidence",
    ],
}


def build_video_segment_user_prompt(
    *,
    previous_global_summary: str,
    previous_timeline: list[dict],
    frame_infos: Iterable[dict],
    background_context: str | None = None,
    background_context_prompt: str | None = None,
    custom_segment_prompt: str | None = None,
    default_segment_prompt: str | None = None,
) -> str:
    current_frame_info = list(frame_infos)
    payload = {
        "previous_global_summary": previous_global_summary or "",
        "previous_timeline": previous_timeline or [],
        "current_frame_info": current_frame_info,
    }
    base_prompt = (
        (custom_segment_prompt or "").strip()
        or (default_segment_prompt or "").strip()
        or VIDEO_SEGMENT_ANALYSIS_USER_PROMPT
    )
    sections = [
        base_prompt,
        "输入 JSON：",
        _dump_prompt_payload(payload),
        "按 schema 返回严格 JSON。",
    ]
    if background_context:
        sections.append(
            "目录背景补充，仅作理解用途和关键词参考；如果与画面冲突，以画面为准：\n"
            f"{background_context.strip()}"
        )
        if background_context_prompt:
            sections.append(f"背景使用规则：\n{background_context_prompt.strip()}")
    return "\n\n".join(sections)


def build_video_final_summary_user_prompt(
    *,
    duration_seconds: float | None,
    segments: Iterable[dict],
    rolling_global_summary: str,
    rolling_timeline: list[dict],
    background_context: str | None = None,
    background_context_prompt: str | None = None,
    custom_final_prompt: str | None = None,
    default_final_prompt: str | None = None,
) -> str:
    payload = {
        "duration_seconds": duration_seconds,
        "rolling_global_summary": rolling_global_summary or "",
        "rolling_timeline": rolling_timeline or [],
        "segments": list(segments),
    }
    base_prompt = (
        (custom_final_prompt or "").strip()
        or (default_final_prompt or "").strip()
        or VIDEO_FINAL_SUMMARY_USER_PROMPT
    )
    sections = [
        base_prompt,
        "输入 JSON：",
        _dump_prompt_payload(payload),
        "按 schema 返回严格 JSON。",
    ]
    if background_context:
        sections.append(
            "目录背景补充，仅作理解用途和关键词参考；如果与分段内容冲突，以分段内容为准：\n"
            f"{background_context.strip()}"
        )
        if background_context_prompt:
            sections.append(f"背景使用规则：\n{background_context_prompt.strip()}")
    return "\n\n".join(sections)


def _dump_prompt_payload(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
