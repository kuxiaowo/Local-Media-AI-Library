from __future__ import annotations

import json
from collections.abc import Iterable


VIDEO_SEGMENT_ANALYSIS_SYSTEM_PROMPT = """
你是一个视频分析助手，所有用户可见文本使用简体中文。只返回符合 schema 的 JSON，不输出解释。
""".strip()

VIDEO_SEGMENT_ANALYSIS_USER_PROMPT = """
你正在分析视频中的一个关键帧批次。帧已经按时间顺序排列。

目标：
- 只依据当前批次图片描述这一小段发生了什么。
- 使用 previous_global_summary 作为历史记忆，更新从视频开头到当前批次为止的全局记忆。
- 不做 OCR，不识别或摘录画面文字。
- 不要输出逐事件列表。

使用规则：
- current_segment_summary 只描述当前批次关键帧可见、可判断，或由当前批次连续画面支持的内容。
- important_observations 只保留对理解视频有帮助的观察。
- updated_global_summary 必须简短，约 150-250 字，保留到目前为止的视频整体脉络。
- uncertain_points 记录无法确定、需要人工复核的内容；没有则返回空数组。
- 如果只能从离散关键帧判断，请使用“画面显示”“似乎”“可能”等谨慎表述，不要虚构关键帧之间未观察到的过程。
""".strip()

VIDEO_SEGMENT_OUTPUT_CONTRACT = """
当前输出契约：
- 只返回严格 JSON，不要输出解释。
- 禁止返回旧的逐事件列表字段、OCR 字段、画面文字字段或滚动时间线字段。
- 必须返回字段：current_segment_summary, important_observations, updated_global_summary, uncertain_points, current_segment_tags, important_objects, new_objects_or_scenes, confidence。
- updated_global_summary 控制在 150-250 字左右。
""".strip()

VIDEO_SEGMENT_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "current_segment_summary": {"type": "string"},
        "important_observations": {"type": "array", "items": {"type": "string"}},
        "updated_global_summary": {"type": "string"},
        "uncertain_points": {"type": "array", "items": {"type": "string"}},
        "current_segment_tags": {"type": "array", "items": {"type": "string"}},
        "important_objects": {"type": "array", "items": {"type": "string"}},
        "new_objects_or_scenes": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
    "required": [
        "current_segment_summary",
        "important_observations",
        "updated_global_summary",
        "uncertain_points",
        "current_segment_tags",
        "important_objects",
        "new_objects_or_scenes",
        "confidence",
    ],
}

VIDEO_FINAL_SUMMARY_SYSTEM_PROMPT = """
你是一个视频分析助手，所有用户可见文本使用简体中文。只返回符合 schema 的 JSON，不输出解释。
""".strip()

VIDEO_FINAL_SUMMARY_USER_PROMPT = """
下面的 JSON 包含分段级视频理解结果。请以 segments[].current_segment_summary、segments[].important_observations 和 final_global_summary 作为主要信息来源。

整合所有 segments，生成视频级结构化摘要：标题、简短摘要、详细摘要、时间线、整体场景、重要物体、动作/主要事件、可能需要人工复核的不确定点、搜索关键词和置信度。
short_summary 做整体概括；detailed_summary 必须按时间顺序描述视频中发生了什么，不要只做整体概括。
保留重要可观察细节，避免重复，不要补充分段结果未支持的事件。
不要做 OCR，不要生成或补充画面文字识别结果。

只返回严格 JSON。timeline 必须覆盖视频中的重要片段。
必须返回字段：title, short_summary, detailed_summary, timeline, scene, objects, actions, uncertain_points, search_keywords, confidence。
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
        "uncertain_points": {"type": "array", "items": {"type": "string"}},
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
        "uncertain_points",
        "search_keywords",
        "confidence",
    ],
}


def build_video_segment_user_prompt(
    *,
    previous_global_summary: str,
    frame_infos: Iterable[dict],
    background_context: str | None = None,
    background_context_prompt: str | None = None,
    custom_segment_prompt: str | None = None,
    default_segment_prompt: str | None = None,
) -> str:
    current_frame_info = list(frame_infos)
    payload = {
        "previous_global_summary": previous_global_summary or "暂无历史信息",
        "current_frame_info": current_frame_info,
    }
    base_prompt = (
        (custom_segment_prompt or "").strip()
        or (default_segment_prompt or "").strip()
        or VIDEO_SEGMENT_ANALYSIS_USER_PROMPT
    )
    sections = [
        base_prompt,
        VIDEO_SEGMENT_OUTPUT_CONTRACT,
        "输入 JSON：",
        _dump_prompt_payload(payload),
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
    final_global_summary: str,
    background_context: str | None = None,
    background_context_prompt: str | None = None,
    custom_final_prompt: str | None = None,
    default_final_prompt: str | None = None,
) -> str:
    payload = {
        "duration_seconds": duration_seconds,
        "final_global_summary": final_global_summary or "",
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
