from __future__ import annotations

import json
from collections.abc import Iterable


VIDEO_SEGMENT_ANALYSIS_SYSTEM_PROMPT = """
你是一个视频分析助手，所有内容使用简体中文。只返回符合 schema 的 JSON，不输出解释。
""".strip()

VIDEO_SEGMENT_ANALYSIS_USER_PROMPT = """
你正在分析视频中的一个关键帧批次。帧已经按时间顺序排列。

请输出以下内容：
- 当前片段描述：概括这一批关键帧中能看到、能判断的场景、动作、状态变化或人物/物体关系。
- 重要观察：列出对理解整个视频有帮助的观察，例如关键物体、场景变化、人物行为、明显状态变化。
- 更新后的全局记忆：结合 previous_global_summary 与当前片段，写出从视频开头到当前批次为止的简短整体记忆。
- 不确定点：列出无法从当前关键帧确认、需要人工复核的内容；没有则返回空数组。
- 标签、重要物体、新场景/新物体和置信度：用于后续搜索和最终汇总。

输出字段内容要求：
- current_segment_summary：只写当前批次图片支持的片段描述，不要写历史批次中才出现的内容。
- important_observations：数组，每项是一条简短客观观察，只保留有助于理解视频的内容。
- updated_global_summary：字符串，约 150-250 字，保留目前为止的视频主线，不要无限追加细节。
- uncertain_points：数组，每项是一条不确定或需要人工复核的问题。
- current_segment_tags：数组，描述当前片段的动作、主题或状态标签。
- important_objects：数组，列出当前片段中对理解有帮助的物体、人物类别或场景元素。
- new_objects_or_scenes：数组，列出相对历史记忆中新出现或明显变化的物体/场景；没有则返回空数组。
- confidence：0 到 1 的数字，表示你对当前片段理解的信心。

时间说明：current_frame_info[].timestamp_seconds 是视频内秒数；timestamp 是同一时间的 HH:MM:SS 格式。

限制：
- 只返回严格 JSON。
- 如果只能从离散关键帧判断，请使用“画面显示”“似乎”“可能”等谨慎表述，不要虚构关键帧之间未观察到的过程。
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
你是一个视频分析助手，所有内容文本使用简体中文。只返回符合 schema 的 JSON，不输出解释。
""".strip()

VIDEO_FINAL_SUMMARY_USER_PROMPT = """
下面的 JSON 包含分段级视频理解结果。请以 segments[].current_segment_summary、segments[].important_observations 和 final_global_summary 作为主要信息来源。

请输出以下内容：
- 视频整体内容：用 short_summary 和 detailed_summary 说明视频整体在讲什么、主要场景是什么、整体过程如何发展。
- 按时间顺序的主要事件：用 timeline 按时间顺序列出重要片段，每项覆盖一个主要阶段或关键变化。
- 可能需要人工复核的不确定点：汇总各分段 uncertain_points，并补充最终总结中仍无法确认的内容。
- 搜索线索：输出整体场景、重要物体、动作/主要事件、搜索关键词和置信度。

输出字段内容要求：
- title：简短标题，概括视频主题。
- short_summary：一到两句话概括视频整体内容。
- detailed_summary：按时间顺序描述视频主要过程，不要只写抽象概括。
- timeline：数组，每项包含 start_time、end_time、summary，覆盖视频中的重要片段；start_time/end_time 使用 HH:MM:SS。
- scene：整体场景或主要环境。
- objects：数组，列出对理解视频有帮助的重要物体、人物类别或场景元素。
- actions：数组，列出主要动作、状态变化或事件主题。
- uncertain_points：数组，列出需要人工复核的不确定点；没有则返回空数组。
- search_keywords：数组，列出适合语义搜索的关键词。
- confidence：high、medium 或 low。

限制：
- 只返回严格 JSON。
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
