from __future__ import annotations

import uuid

from sqlalchemy import and_, false, or_, select
from sqlalchemy.orm import Session

from app.models.db_models import DirectoryRule, MediaFile


def visible_media_filter(db: Session):
    rule_ids, root_paths = _enabled_rule_refs(db)
    if not rule_ids:
        return false()
    return or_(
        MediaFile.folder_rule_id.in_(list(rule_ids)),
        and_(MediaFile.folder_rule_id.is_(None), MediaFile.root_path.in_(list(root_paths))),
    )


def is_media_visible(db: Session, media: MediaFile) -> bool:
    rule_ids, root_paths = _enabled_rule_refs(db)
    if not rule_ids:
        return False
    if media.folder_rule_id in rule_ids:
        return True
    return media.folder_rule_id is None and media.root_path in root_paths


def _enabled_rule_refs(db: Session) -> tuple[set[uuid.UUID], set[str]]:
    rows = db.execute(
        select(DirectoryRule.id, DirectoryRule.normalized_path).where(DirectoryRule.enabled)
    ).all()
    return {row[0] for row in rows}, {row[1] for row in rows}
