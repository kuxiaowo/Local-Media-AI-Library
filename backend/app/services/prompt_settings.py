from __future__ import annotations

from pathlib import Path

from app.config import get_settings
from app.prompts.image_analysis import BACKGROUND_CONTEXT_PROMPT, IMAGE_ANALYSIS_SYSTEM_PROMPT, IMAGE_ANALYSIS_USER_PROMPT
from app.prompts.video_analysis import (
    VIDEO_FINAL_SUMMARY_SYSTEM_PROMPT,
    VIDEO_FINAL_SUMMARY_USER_PROMPT,
    VIDEO_SEGMENT_ANALYSIS_SYSTEM_PROMPT,
    VIDEO_SEGMENT_ANALYSIS_USER_PROMPT,
)


def get_default_analysis_prompt() -> str:
    path = _default_prompt_path()
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return IMAGE_ANALYSIS_USER_PROMPT


def get_default_analysis_system_prompt() -> str:
    path = _default_system_prompt_path()
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return IMAGE_ANALYSIS_SYSTEM_PROMPT


def update_default_analysis_system_prompt(prompt: str) -> str:
    text = prompt.strip()
    if not text:
        raise ValueError("Default analysis system prompt cannot be empty")
    path = _default_system_prompt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    return text


def reset_default_analysis_system_prompt() -> str:
    path = _default_system_prompt_path()
    if path.exists():
        path.unlink()
    return IMAGE_ANALYSIS_SYSTEM_PROMPT


def update_default_analysis_prompt(prompt: str) -> str:
    text = prompt.strip()
    if not text:
        raise ValueError("Default analysis prompt cannot be empty")
    path = _default_prompt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    return text


def reset_default_analysis_prompt() -> str:
    path = _default_prompt_path()
    if path.exists():
        path.unlink()
    return IMAGE_ANALYSIS_USER_PROMPT


def get_default_background_context_prompt() -> str:
    path = _default_background_context_prompt_path()
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return BACKGROUND_CONTEXT_PROMPT


def update_default_background_context_prompt(prompt: str) -> str:
    text = prompt.strip()
    if not text:
        raise ValueError("Default background context prompt cannot be empty")
    path = _default_background_context_prompt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    return text


def reset_default_background_context_prompt() -> str:
    path = _default_background_context_prompt_path()
    if path.exists():
        path.unlink()
    return BACKGROUND_CONTEXT_PROMPT


def get_default_video_segment_system_prompt() -> str:
    path = _default_video_segment_system_prompt_path()
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return VIDEO_SEGMENT_ANALYSIS_SYSTEM_PROMPT


def update_default_video_segment_system_prompt(prompt: str) -> str:
    text = prompt.strip()
    if not text:
        raise ValueError("Default video segment system prompt cannot be empty")
    path = _default_video_segment_system_prompt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    return text


def reset_default_video_segment_system_prompt() -> str:
    path = _default_video_segment_system_prompt_path()
    if path.exists():
        path.unlink()
    return VIDEO_SEGMENT_ANALYSIS_SYSTEM_PROMPT


def get_default_video_segment_prompt() -> str:
    path = _default_video_segment_prompt_path()
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return VIDEO_SEGMENT_ANALYSIS_USER_PROMPT


def update_default_video_segment_prompt(prompt: str) -> str:
    text = prompt.strip()
    if not text:
        raise ValueError("Default video segment prompt cannot be empty")
    path = _default_video_segment_prompt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    return text


def reset_default_video_segment_prompt() -> str:
    path = _default_video_segment_prompt_path()
    if path.exists():
        path.unlink()
    return VIDEO_SEGMENT_ANALYSIS_USER_PROMPT


def get_default_video_final_summary_system_prompt() -> str:
    path = _default_video_final_summary_system_prompt_path()
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return VIDEO_FINAL_SUMMARY_SYSTEM_PROMPT


def update_default_video_final_summary_system_prompt(prompt: str) -> str:
    text = prompt.strip()
    if not text:
        raise ValueError("Default video final summary system prompt cannot be empty")
    path = _default_video_final_summary_system_prompt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    return text


def reset_default_video_final_summary_system_prompt() -> str:
    path = _default_video_final_summary_system_prompt_path()
    if path.exists():
        path.unlink()
    return VIDEO_FINAL_SUMMARY_SYSTEM_PROMPT


def get_default_video_final_summary_prompt() -> str:
    path = _default_video_final_summary_prompt_path()
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return VIDEO_FINAL_SUMMARY_USER_PROMPT


def update_default_video_final_summary_prompt(prompt: str) -> str:
    text = prompt.strip()
    if not text:
        raise ValueError("Default video final summary prompt cannot be empty")
    path = _default_video_final_summary_prompt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    return text


def reset_default_video_final_summary_prompt() -> str:
    path = _default_video_final_summary_prompt_path()
    if path.exists():
        path.unlink()
    return VIDEO_FINAL_SUMMARY_USER_PROMPT


def _default_prompt_path() -> Path:
    settings = get_settings()
    return settings.cache_dir.parent / "config" / "default_image_analysis_prompt.txt"


def _default_system_prompt_path() -> Path:
    settings = get_settings()
    return settings.cache_dir.parent / "config" / "default_image_analysis_system_prompt.txt"


def _default_background_context_prompt_path() -> Path:
    settings = get_settings()
    return settings.cache_dir.parent / "config" / "default_background_context_prompt.txt"


def _default_video_segment_system_prompt_path() -> Path:
    settings = get_settings()
    return settings.cache_dir.parent / "config" / "default_video_segment_system_prompt.txt"


def _default_video_segment_prompt_path() -> Path:
    settings = get_settings()
    return settings.cache_dir.parent / "config" / "default_video_segment_prompt.txt"


def _default_video_final_summary_system_prompt_path() -> Path:
    settings = get_settings()
    return settings.cache_dir.parent / "config" / "default_video_final_summary_system_prompt.txt"


def _default_video_final_summary_prompt_path() -> Path:
    settings = get_settings()
    return settings.cache_dir.parent / "config" / "default_video_final_summary_prompt.txt"
