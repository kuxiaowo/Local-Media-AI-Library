IMAGE_ANALYSIS_SYSTEM_PROMPT = """
你是一个图片识别助手，所有用户可见文本使用简体中文。只返回符合 schema 的 JSON，不输出解释。
""".strip()

IMAGE_ANALYSIS_USER_PROMPT = """
请分析这张图片，并只返回一个 JSON 对象。

分析原则：
1. 使用简体中文描述图片内容。
2. 只描述图片中可见、可判断的内容，不要编造不可见信息。
3. 标题必须简短、客观。如果图片中没有可见标题，请根据主要可见主体生成，例如“室内人物合影”“街景与车辆”“桌面物品特写”。
4. 尽量保留便于搜索的关键词，尤其是场景、物体、动作、可见文字、风格、氛围、用途。

必须返回下面这些 JSON 字段：
- title: string，简短中文标题，不能为空。
- short_summary: string，一句话中文摘要，不能为空。
- detailed_summary: string，较完整的中文描述，说明主体、场景、动作、物体、可见文字和氛围，不能为空。
- scene: string，场景或环境，例如“室内房间”“街道”“桌面”“截图界面”，不确定时写“未知场景”。
- objects: string[]，主要可见物体列表。
- people: string[]，可见人物的客观描述列表；不要推断身份、姓名、年龄、职业、国籍、宗教或政治观点。
- actions: string[]，可见动作或事件列表。
- text_visible: string[]，图片中可读文字列表；没有则返回空数组。
- location_guess: string，基于可见内容的地点线索；不确定时写“unknown”。
- time_clues: string，基于可见内容或 EXIF 以外画面线索的时间线索；不确定时写“unknown”。
- mood: string，画面氛围或情绪，例如“安静”“热闹”“明亮”“unknown”。
- search_keywords: string[]，适合搜索的中文关键词和短语。
- confidence: string，只能是 high、medium、low。

输出要求：
1. 只输出 JSON 对象本身。
2. 不要输出 Markdown 代码块。
3. 不要输出解释、前缀、后缀或注释。
4. 不要遗漏字段。
""".strip()


BACKGROUND_CONTEXT_PROMPT = """
请把目录背景补充只作为理解图片用途、拍摄场景、命名习惯或搜索关键词的参考。
如果背景补充与图片可见内容冲突，必须以图片可见内容为准。
不要把背景补充中没有被图片支持的内容当作事实写入摘要。
""".strip()


def build_image_analysis_user_prompt(
    *,
    custom_analysis_prompt: str | None = None,
    background_context: str | None = None,
    background_context_prompt: str | None = None,
    default_analysis_prompt: str | None = None,
    default_background_context_prompt: str | None = None,
    source_filename: str | None = None,
) -> str:
    base_prompt = (
        (custom_analysis_prompt or "").strip()
        or (default_analysis_prompt or "").strip()
        or IMAGE_ANALYSIS_USER_PROMPT
    )
    sections = [base_prompt]
    filename = (source_filename or "").strip()

    if filename:
        sections.append(
            "\n文件名：\n"
            f"{filename}\n"
            "\n文件名使用规则：\n"
            "文件名只作为辅助信息，可用于标题、关键词和搜索词；如果文件名和画面冲突，以画面内容为准。"
        )

    background = (background_context or "").strip()

    if background:
        background_prompt = (
            (background_context_prompt or "").strip()
            or (default_background_context_prompt or "").strip()
            or BACKGROUND_CONTEXT_PROMPT
        )
        sections.append(
            "\n目录背景补充：\n"
            f"{background}\n"
            "\n背景使用规则：\n"
            f"{background_prompt}"
        )

    return "\n".join(sections)


IMAGE_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "short_summary": {"type": "string"},
        "detailed_summary": {"type": "string"},
        "scene": {"type": "string"},
        "objects": {"type": "array", "items": {"type": "string"}},
        "people": {"type": "array", "items": {"type": "string"}},
        "actions": {"type": "array", "items": {"type": "string"}},
        "text_visible": {"type": "array", "items": {"type": "string"}},
        "location_guess": {"type": "string"},
        "time_clues": {"type": "string"},
        "mood": {"type": "string"},
        "search_keywords": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": [
        "title",
        "short_summary",
        "detailed_summary",
        "scene",
        "objects",
        "people",
        "actions",
        "text_visible",
        "location_guess",
        "time_clues",
        "mood",
        "search_keywords",
        "confidence",
    ],
}
