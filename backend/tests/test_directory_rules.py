from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes_directory_rules import create_rule, update_rule
from app.database import Base
from app.models.schemas import DirectoryRuleCreate, DirectoryRuleUpdate


def _rule_create_payload(path: str) -> DirectoryRuleCreate:
    return DirectoryRuleCreate(
        path=path,
        recursive=True,
        vision_model="vision-model",
        summary_model="summary-model",
    )


def test_create_rule_saves_display_path_with_forward_slashes() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        rule = create_rule(_rule_create_payload("D:\\Photos\\School\\"), db=db)

    assert rule.path == "D:/Photos/School"


def test_update_rule_saves_display_path_with_forward_slashes() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        rule = create_rule(_rule_create_payload("D:/Photos"), db=db)
        updated = update_rule(rule.id, DirectoryRuleUpdate(path="D:\\Photos\\School\\"), db=db)

    assert updated.path == "D:/Photos/School"
