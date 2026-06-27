from __future__ import annotations

import json
from pathlib import Path

from app.config import get_settings
from app.models.schemas import DirectoryRuleDefaults


def get_directory_rule_defaults() -> DirectoryRuleDefaults:
    fallback = _fallback_defaults()
    path = _defaults_path()
    if not path.exists():
        return fallback

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Directory rule defaults file is invalid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("Directory rule defaults file must contain an object")
    return DirectoryRuleDefaults.model_validate({**fallback.model_dump(), **payload})


def update_directory_rule_defaults(payload: DirectoryRuleDefaults) -> DirectoryRuleDefaults:
    defaults = DirectoryRuleDefaults.model_validate(payload.model_dump())
    path = _defaults_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(defaults.model_dump(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return defaults


def reset_directory_rule_defaults() -> DirectoryRuleDefaults:
    path = _defaults_path()
    if path.exists():
        path.unlink()
    return _fallback_defaults()


def _fallback_defaults() -> DirectoryRuleDefaults:
    settings = get_settings()
    return DirectoryRuleDefaults(
        recursive=True,
        vision_model=settings.default_vision_model,
        summary_model=settings.default_summary_model,
        video_frame_strategy="hybrid",
        frame_interval_seconds=settings.default_frame_interval_seconds,
        max_frames_per_video=settings.default_max_frames_per_video,
        video_frame_max_width=settings.default_video_frame_max_width,
        video_frame_max_height=settings.default_video_frame_max_height,
        video_batch_size=settings.default_video_batch_size,
        video_batch_overlap=settings.default_video_batch_overlap,
        analysis_detail="normal",
        enabled=True,
    )


def _defaults_path() -> Path:
    settings = get_settings()
    return settings.cache_dir.parent / "config" / "directory_rule_defaults.json"
