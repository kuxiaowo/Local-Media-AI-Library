from app.prompts.image_analysis import IMAGE_ANALYSIS_USER_PROMPT, build_image_analysis_user_prompt


def test_build_image_analysis_prompt_uses_default_when_empty() -> None:
    prompt = build_image_analysis_user_prompt()
    assert IMAGE_ANALYSIS_USER_PROMPT in prompt
    assert "title: string" in prompt
    assert "search_keywords: string[]" in prompt
    assert "固定输出要求" in prompt


def test_build_image_analysis_prompt_uses_directory_prompt_as_base() -> None:
    prompt = build_image_analysis_user_prompt(
        custom_analysis_prompt="请重点描述服装和动作。",
        background_context="这个目录是活动照片。",
    )
    assert "请重点描述服装和动作。" in prompt
    assert IMAGE_ANALYSIS_USER_PROMPT not in prompt
    assert "这个目录是活动照片。" in prompt
    assert "固定输出要求" in prompt


def test_build_image_analysis_prompt_uses_editable_default_prompt() -> None:
    prompt = build_image_analysis_user_prompt(default_analysis_prompt="默认提示词 A")
    assert "默认提示词 A" in prompt
    assert IMAGE_ANALYSIS_USER_PROMPT not in prompt
