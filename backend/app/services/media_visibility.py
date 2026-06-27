from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

from sqlalchemy import and_, false, not_, or_, select
from sqlalchemy.orm import Session

from app.core.path_utils import normalize_path, path_has_prefix
from app.models.db_models import DirectoryRule, MediaFile

RuleT = TypeVar("RuleT")


@dataclass(frozen=True)
class RuleRef:
    normalized_path: str
    enabled: bool


def visible_media_filter(db: Session):
    visible_clauses = _visible_path_clauses(_rule_refs(db))
    if not visible_clauses:
        return false()
    return or_(*visible_clauses)


def is_media_visible(db: Session, media: MediaFile) -> bool:
    return is_path_visible(media.normalized_path, _rule_refs(db))


def is_path_visible(path: str, rules: list[RuleRef]) -> bool:
    matching_rules = [rule for rule in rules if path_has_prefix(path, rule.normalized_path)]
    return bool(matching_rules) and all(rule.enabled for rule in matching_rules)


def effective_enabled_rules(rules: list[RuleT]) -> list[RuleT]:
    refs = [(rule, _rule_ref_from_object(rule)) for rule in rules]
    disabled_refs = [ref for _rule, ref in refs if not ref.enabled]
    return [
        rule
        for rule, ref in refs
        if ref.enabled and not _has_disabled_ancestor(ref, disabled_refs)
    ]


def is_rule_effectively_enabled(rule: RuleT, rules: list[RuleT]) -> bool:
    ref = _rule_ref_from_object(rule)
    if not ref.enabled:
        return False
    disabled_refs = [
        _rule_ref_from_object(item)
        for item in rules
        if not getattr(item, "enabled", False)
    ]
    return not _has_disabled_ancestor(ref, disabled_refs)


def _rule_refs(db: Session) -> list[RuleRef]:
    rows = db.execute(
        select(DirectoryRule.normalized_path, DirectoryRule.enabled)
    ).all()
    return [
        RuleRef(normalized_path=normalize_path(row[0]), enabled=bool(row[1]))
        for row in rows
    ]


def _visible_path_clauses(rules: list[RuleRef]):
    clauses = []
    disabled_rules = [rule for rule in rules if not rule.enabled]
    for rule in rules:
        if not rule.enabled or _has_disabled_ancestor(rule, disabled_rules):
            continue
        shadowing_disabled_rules = [
            disabled_rule
            for disabled_rule in disabled_rules
            if _is_descendant_rule(disabled_rule, rule)
        ]
        path_clause = _path_prefix_clause(rule.normalized_path)
        if shadowing_disabled_rules:
            disabled_clause = or_(*(_path_prefix_clause(item.normalized_path) for item in shadowing_disabled_rules))
            path_clause = and_(path_clause, not_(disabled_clause))
        clauses.append(path_clause)
    return clauses


def _path_prefix_clause(path: str):
    normalized = normalize_path(path)
    return or_(
        MediaFile.normalized_path == normalized,
        MediaFile.normalized_path.like(f"{_escape_like(normalized)}/%", escape="\\"),
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _rule_ref_from_object(rule: object) -> RuleRef:
    return RuleRef(
        normalized_path=normalize_path(getattr(rule, "normalized_path", None) or getattr(rule, "path")),
        enabled=bool(getattr(rule, "enabled", False)),
    )


def _has_disabled_ancestor(rule: RuleRef, disabled_rules: list[RuleRef]) -> bool:
    return any(_is_descendant_rule(rule, disabled_rule) for disabled_rule in disabled_rules)


def _is_descendant_rule(child: RuleRef, parent: RuleRef) -> bool:
    return (
        len(child.normalized_path) > len(parent.normalized_path)
        and path_has_prefix(child.normalized_path, parent.normalized_path)
    )
