from app.prompts.image_analysis import (
    BACKGROUND_CONTEXT_PROMPT,
    IMAGE_ANALYSIS_SYSTEM_PROMPT,
    IMAGE_ANALYSIS_USER_PROMPT,
    build_image_analysis_user_prompt,
)
from app.services import prompt_settings


def test_build_image_analysis_prompt_uses_default_when_empty() -> None:
    prompt = build_image_analysis_user_prompt()
    assert IMAGE_ANALYSIS_USER_PROMPT in prompt
    assert "title: string" in prompt
    assert "search_keywords: string[]" in prompt
    assert "目录背景补充" not in prompt
    assert BACKGROUND_CONTEXT_PROMPT not in prompt
    assert "输出要求" in prompt


def test_build_image_analysis_prompt_uses_directory_prompt_as_base() -> None:
    prompt = build_image_analysis_user_prompt(
        custom_analysis_prompt="请重点描述服装和动作。",
        background_context="这个目录是活动照片。",
    )
    assert "请重点描述服装和动作。" in prompt
    assert IMAGE_ANALYSIS_USER_PROMPT not in prompt
    assert "这个目录是活动照片。" in prompt
    assert BACKGROUND_CONTEXT_PROMPT in prompt


def test_build_image_analysis_prompt_uses_editable_default_prompt() -> None:
    prompt = build_image_analysis_user_prompt(default_analysis_prompt="默认提示词 A")
    assert "默认提示词 A" in prompt
    assert IMAGE_ANALYSIS_USER_PROMPT not in prompt


def test_build_image_analysis_prompt_includes_source_filename() -> None:
    prompt = build_image_analysis_user_prompt(source_filename="sample-video-01.mp4")
    assert "文件名：" in prompt
    assert "sample-video-01.mp4" in prompt
    assert "文件名线索" not in prompt


def test_build_image_analysis_prompt_uses_directory_background_prompt() -> None:
    prompt = build_image_analysis_user_prompt(
        background_context="这个目录是商品图。",
        default_background_context_prompt="默认背景规则",
        background_context_prompt="目录背景规则",
    )
    assert "这个目录是商品图。" in prompt
    assert "目录背景规则" in prompt
    assert "默认背景规则" not in prompt


def test_image_analysis_system_prompt_can_be_updated_and_reset(tmp_path, monkeypatch) -> None:
    prompt_path = tmp_path / "default_image_analysis_system_prompt.txt"
    monkeypatch.setattr(prompt_settings, "_default_system_prompt_path", lambda: prompt_path)

    assert prompt_settings.get_default_analysis_system_prompt() == IMAGE_ANALYSIS_SYSTEM_PROMPT
    assert prompt_settings.update_default_analysis_system_prompt("自定义图片 system") == "自定义图片 system"
    assert prompt_settings.get_default_analysis_system_prompt() == "自定义图片 system"
    assert prompt_settings.reset_default_analysis_system_prompt() == IMAGE_ANALYSIS_SYSTEM_PROMPT
    assert not prompt_path.exists()
