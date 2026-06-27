from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes_directory_rules import create_rule, update_rule
from app.database import Base
from app.models.db_models import DirectoryRule
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


def test_disabling_rule_cascades_to_descendant_rules() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        parent = create_rule(_rule_create_payload("D:/Photos"), db=db)
        child = create_rule(_rule_create_payload("D:/Photos/School"), db=db)

        update_rule(parent.id, DirectoryRuleUpdate(enabled=False), db=db)

        db.refresh(parent)
        db.refresh(child)

    assert parent.enabled is False
    assert child.enabled is False


def test_child_rule_cannot_be_enabled_under_disabled_parent() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        disabled_parent = DirectoryRule(
            path="D:/Photos",
            normalized_path="d:/photos",
            recursive=True,
            vision_model="vision-model",
            summary_model="summary-model",
            video_frame_strategy="hybrid",
            frame_interval_seconds=5,
            max_frames_per_video=12,
            video_frame_max_width=1280,
            video_batch_size=6,
            video_batch_overlap=1,
            analysis_detail="normal",
            enabled=False,
        )
        db.add(disabled_parent)
        db.commit()

        child = create_rule(_rule_create_payload("D:/Photos/School"), db=db)

    assert child.enabled is False
