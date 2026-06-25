from __future__ import annotations

import hashlib
import json
from typing import Protocol

from app.core.path_utils import normalize_path, path_has_prefix


class RuleLike(Protocol):
    path: str
    normalized_path: str
    enabled: bool
    vision_model: str
    summary_model: str
    custom_analysis_prompt: str | None
    background_context: str | None
    background_context_prompt: str | None
    video_segment_prompt: str | None
    video_final_summary_prompt: str | None
    video_frame_strategy: str
    frame_interval_seconds: int
    max_frames_per_video: int
    video_frame_max_width: int
    video_frame_max_height: int | None
    video_batch_size: int
    analysis_detail: str


def resolve_rule(file_path: str, rules: list[RuleLike]) -> RuleLike | None:
    candidates = []
    normalized = normalize_path(file_path)
    for rule in rules:
        if not rule.enabled:
            continue
        rule_path = getattr(rule, "normalized_path", None) or normalize_path(rule.path)
        if path_has_prefix(normalized, rule_path):
            candidates.append(rule)
    if not candidates:
        return None
    return max(candidates, key=lambda rule: len(getattr(rule, "normalized_path", None) or rule.path))


def rule_config_hash(rule: RuleLike) -> str:
    payload = {
        "vision_model": rule.vision_model,
        "summary_model": rule.summary_model,
        "custom_analysis_prompt": getattr(rule, "custom_analysis_prompt", None) or "",
        "background_context": getattr(rule, "background_context", None) or "",
        "background_context_prompt": getattr(rule, "background_context_prompt", None) or "",
        "video_segment_prompt": getattr(rule, "video_segment_prompt", None) or "",
        "video_final_summary_prompt": getattr(rule, "video_final_summary_prompt", None) or "",
        "video_frame_strategy": rule.video_frame_strategy,
        "frame_interval_seconds": rule.frame_interval_seconds,
        "max_frames_per_video": rule.max_frames_per_video,
        "video_frame_max_width": rule.video_frame_max_width,
        "video_frame_max_height": rule.video_frame_max_height,
        "video_batch_size": rule.video_batch_size,
        "analysis_detail": rule.analysis_detail,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
