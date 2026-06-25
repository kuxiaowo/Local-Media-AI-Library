from __future__ import annotations

from pathlib import Path

from app.config import get_settings
from app.prompts.image_analysis import IMAGE_ANALYSIS_USER_PROMPT


def get_default_analysis_prompt() -> str:
    path = _default_prompt_path()
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return IMAGE_ANALYSIS_USER_PROMPT


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


def _default_prompt_path() -> Path:
    settings = get_settings()
    return settings.cache_dir.parent / "config" / "default_image_analysis_prompt.txt"
