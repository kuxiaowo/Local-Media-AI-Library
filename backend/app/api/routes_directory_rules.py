from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.path_utils import normalize_path, path_has_prefix
from app.database import get_db
from app.models.db_models import DirectoryRule, MediaFile
from app.models.schemas import DirectoryRuleCreate, DirectoryRuleRead, DirectoryRuleUpdate
from app.services.rule_resolver import rule_config_hash

router = APIRouter(prefix="/directory-rules", tags=["directory-rules"])


@router.get("", response_model=list[DirectoryRuleRead])
def list_rules(db: Session = Depends(get_db)) -> list[DirectoryRule]:
    return list(db.scalars(select(DirectoryRule).order_by(DirectoryRule.normalized_path)).all())


@router.post("", response_model=DirectoryRuleRead)
def create_rule(payload: DirectoryRuleCreate, db: Session = Depends(get_db)) -> DirectoryRule:
    data = payload.model_dump()
    data["path"] = _display_path(data["path"])
    normalized = normalize_path(data["path"])
    if db.scalar(select(DirectoryRule).where(DirectoryRule.normalized_path == normalized)):
        raise HTTPException(status_code=409, detail="Directory rule already exists")
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
    db.delete(rule)
    db.commit()


def _display_path(path: str) -> str:
    text = str(path).strip().strip('"').replace("\\", "/")
    if re.fullmatch(r"[A-Za-z]:/*", text):
        return f"{text[0].upper()}:/"
    if text.startswith("//"):
        return "//" + text[2:].rstrip("/")
    return text.rstrip("/") or text
