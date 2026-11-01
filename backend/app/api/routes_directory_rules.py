from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.path_utils import normalize_path, path_has_prefix
from app.database import get_db
from app.models.db_models import DirectoryRule, MediaFile
from app.models.schemas import (
    DirectoryRuleCreate,
    DirectoryRuleDefaults,
    DirectoryRuleRead,
    DirectoryRuleUpdate,
)
from app.services.directory_defaults import get_directory_rule_defaults
from app.services.prompt_settings import (
    get_default_analysis_prompt,
    get_default_background_context_prompt,
    get_default_video_final_summary_prompt,
    get_default_video_segment_prompt,
)
from app.services.rule_resolver import rule_config_hash

router = APIRouter(prefix="/directory-rules", tags=["directory-rules"])


@router.get("", response_model=list[DirectoryRuleRead])
def list_rules(existing_only: bool = False, db: Session = Depends(get_db)) -> list[DirectoryRule]:
    rules = list(db.scalars(select(DirectoryRule).order_by(DirectoryRule.normalized_path)).all())
    if not existing_only:
        return rules
    return [rule for rule in rules if _directory_exists(rule.path)]


@router.post("", response_model=DirectoryRuleRead)
def create_rule(payload: DirectoryRuleCreate, db: Session = Depends(get_db)) -> DirectoryRule:
    data = payload.model_dump()
    data["path"] = _display_path(data["path"])
    normalized = normalize_path(data["path"])
    existing = db.scalar(select(DirectoryRule).where(DirectoryRule.normalized_path == normalized))
    if existing is not None:
        if _directory_exists(existing.path):
            raise HTTPException(status_code=409, detail="Directory rule already exists")
        _detach_rule_media(db, existing)
        db.delete(existing)
        db.flush()
    if db.scalar(select(DirectoryRule).where(DirectoryRule.normalized_path == normalized)):
        raise HTTPException(status_code=409, detail="Directory rule already exists")

    rules = list(db.scalars(select(DirectoryRule)).all())
    _create_missing_intermediate_rules(
        db,
        target_path=data["path"],
        target_normalized=normalized,
        rules=rules,
    )
    rules = list(db.scalars(select(DirectoryRule)).all())
    if data.get("enabled", True) and _has_disabled_ancestor_path(normalized, rules):
        data["enabled"] = False
    rule = DirectoryRule(**data, normalized_path=normalized)
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.put("/{rule_id}", response_model=DirectoryRuleRead)
def update_rule(
    rule_id: uuid.UUID,
    payload: DirectoryRuleUpdate,
    db: Session = Depends(get_db),
) -> DirectoryRule:
    rule = db.get(DirectoryRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Directory rule not found")
    before_hash = rule_config_hash(rule)
    data = payload.model_dump(exclude_unset=True)
    if "path" in data and data["path"]:
        data["path"] = _display_path(data["path"])
        data["normalized_path"] = normalize_path(data["path"])
    for key, value in data.items():
        setattr(rule, key, value)
    all_rules = list(db.scalars(select(DirectoryRule)).all())
    if rule.enabled and _has_disabled_ancestor_path(rule.normalized_path, all_rules, exclude_id=rule.id):
        rule.enabled = False
    if not rule.enabled:
        _disable_descendant_rules(rule, all_rules)
    after_hash = rule_config_hash(rule)
    if before_hash != after_hash:
        affected = db.scalars(
            select(MediaFile).where(MediaFile.status.in_(("done", "embedding_pending")))
        ).all()
        for media in affected:
            if path_has_prefix(media.normalized_path, rule.normalized_path):
                media.status = "needs_reanalysis"
                media.error_message = (
                    "Directory rule changed; previous analysis is retained until reanalysis runs"
                )
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    rule = db.get(DirectoryRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Directory rule not found")
    _detach_rule_media(db, rule)
    db.delete(rule)
    db.commit()


def _detach_rule_media(db: Session, rule: DirectoryRule) -> None:
    affected = db.scalars(
        select(MediaFile).where(
            or_(MediaFile.folder_rule_id == rule.id, MediaFile.root_path == rule.normalized_path)
        )
    ).all()
    for media in affected:
        if media.folder_rule_id == rule.id:
            media.folder_rule_id = None
            media.resolved_config_hash = None
        if media.root_path == rule.normalized_path:
            media.root_path = None


def _directory_exists(path: str) -> bool:
    try:
        return Path(path).is_dir()
    except OSError:
        return False


def _display_path(path: str) -> str:
    text = str(path).strip().strip('"').replace("\\", "/")
    if re.fullmatch(r"[A-Za-z]:/*", text):
        return f"{text[0].upper()}:/"
    if text.startswith("//"):
        return "//" + text[2:].rstrip("/")
    return text.rstrip("/") or text


def _create_missing_intermediate_rules(
    db: Session,
    *,
    target_path: str,
    target_normalized: str,
    rules: list[DirectoryRule],
) -> None:
    ancestor = _nearest_existing_ancestor(target_normalized, rules)
    if ancestor is None:
        return

    existing_paths = {rule.normalized_path for rule in rules}
    missing_prefixes = [
        prefix
        for prefix in _directory_path_prefixes(target_normalized, target_path)
        if prefix["normalized_path"] != target_normalized
        and _is_descendant_path(prefix["normalized_path"], ancestor.normalized_path)
        and prefix["normalized_path"] not in existing_paths
    ]
    if not missing_prefixes:
        return

    defaults = get_directory_rule_defaults()
    known_rules = list(rules)
    for prefix in missing_prefixes:
        data = _default_rule_data(prefix["path"], defaults)
        if data.get("enabled", True) and _has_disabled_ancestor_path(
            prefix["normalized_path"],
            known_rules,
        ):
            data["enabled"] = False
        rule = DirectoryRule(**data, normalized_path=prefix["normalized_path"])
        db.add(rule)
        db.flush()
        known_rules.append(rule)
        existing_paths.add(rule.normalized_path)


def _nearest_existing_ancestor(
    target_normalized: str,
    rules: list[DirectoryRule],
) -> DirectoryRule | None:
    ancestors = [
        rule
        for rule in rules
        if _is_descendant_path(target_normalized, rule.normalized_path)
    ]
    if not ancestors:
        return None
    return max(ancestors, key=lambda rule: len(rule.normalized_path))


def _default_rule_data(path: str, defaults: DirectoryRuleDefaults) -> dict[str, object]:
    return {
        "path": _display_path(path),
        "recursive": defaults.recursive,
        "vision_model": defaults.vision_model,
        "summary_model": defaults.summary_model,
        "custom_analysis_prompt": get_default_analysis_prompt(),
        "background_context": "",
        "background_context_prompt": get_default_background_context_prompt(),
        "video_segment_prompt": get_default_video_segment_prompt(),
        "video_final_summary_prompt": get_default_video_final_summary_prompt(),
        "video_frame_strategy": defaults.video_frame_strategy,
        "frame_interval_seconds": defaults.frame_interval_seconds,
        "max_frames_per_video": defaults.max_frames_per_video,
        "video_frame_max_width": defaults.video_frame_max_width,
        "video_frame_max_height": defaults.video_frame_max_height,
        "video_batch_size": defaults.video_batch_size,
        "video_batch_overlap": defaults.video_batch_overlap,
        "analysis_detail": defaults.analysis_detail,
        "enabled": defaults.enabled,
    }


def _directory_path_prefixes(normalized_path: str, display_path: str) -> list[dict[str, str]]:
    normalized = normalize_path(normalized_path)
    display = _display_path(display_path)
    if not normalized:
        return []

    normalized_parts = normalized.split("/")
    display_parts = display.replace("\\", "/").rstrip("/").split("/")
    start_index = _prefix_start_index(normalized, normalized_parts)
    prefixes: list[dict[str, str]] = []
    for index in range(start_index, len(normalized_parts)):
        if not normalized_parts[index]:
            continue
        prefixes.append(
            {
                "normalized_path": normalize_path(_compose_directory_prefix(normalized_parts, index)),
                "path": _display_path(_compose_directory_prefix(display_parts, index)),
            }
        )
    if not prefixes:
        return [{"normalized_path": normalized, "path": display}]
    return prefixes


def _prefix_start_index(path: str, parts: list[str]) -> int:
    if re.fullmatch(r"[a-z]:", parts[0] if parts else "", flags=re.IGNORECASE):
        return 1
    if path.startswith("//"):
        return 3
    if path.startswith("/"):
        return 1
    return 0


def _compose_directory_prefix(parts: list[str], end_index: int) -> str:
    if len(parts) > 1 and parts[0] == "" and parts[1] == "":
        return "//" + "/".join(parts[2 : end_index + 1])
    if parts and parts[0] == "":
        return "/" + "/".join(parts[1 : end_index + 1])
    if parts and re.fullmatch(r"[A-Za-z]:", parts[0]):
        if end_index == 0:
            return f"{parts[0]}/"
        return f"{parts[0]}/{'/'.join(parts[1 : end_index + 1])}"
    return "/".join(parts[: end_index + 1])


def _disable_descendant_rules(rule: DirectoryRule, rules: list[DirectoryRule]) -> None:
    for candidate in rules:
        if candidate.id == rule.id:
            continue
        if _is_descendant_path(candidate.normalized_path, rule.normalized_path):
            candidate.enabled = False


def _has_disabled_ancestor_path(
    normalized_path: str,
    rules: list[DirectoryRule],
    *,
    exclude_id: uuid.UUID | None = None,
) -> bool:
    return any(
        not rule.enabled
        and rule.id != exclude_id
        and _is_descendant_path(normalized_path, rule.normalized_path)
        for rule in rules
    )


def _is_descendant_path(path: str, ancestor_path: str) -> bool:
    return len(path) > len(ancestor_path) and path_has_prefix(path, ancestor_path)
