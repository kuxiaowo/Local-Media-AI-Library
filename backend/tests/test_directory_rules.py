from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.api import routes_directory_rules
from app.api.routes_directory_rules import create_rule, list_rules, update_rule
from app.database import Base
from app.models.db_models import DirectoryRule
from app.models.schemas import DirectoryRuleCreate, DirectoryRuleDefaults, DirectoryRuleUpdate


def _rule_create_payload(path: str) -> DirectoryRuleCreate:
    return DirectoryRuleCreate(
        path=path,
        recursive=True,
        vision_model="vision-model",
        summary_model="summary-model",
    )


def _patch_intermediate_defaults(monkeypatch) -> None:
    monkeypatch.setattr(
        routes_directory_rules,
        "get_directory_rule_defaults",
        lambda: DirectoryRuleDefaults(
            recursive=False,
            vision_model="default-vision",
            summary_model="default-summary",
            video_frame_strategy="fixed_interval",
            frame_interval_seconds=3,
            max_frames_per_video=9,
            video_frame_max_width=640,
            video_frame_max_height=480,
            video_batch_size=2,
            video_batch_overlap=0,
            analysis_detail="brief",
            enabled=True,
        ),
    )
    monkeypatch.setattr(routes_directory_rules, "get_default_analysis_prompt", lambda: "default analysis")
    monkeypatch.setattr(
        routes_directory_rules,
        "get_default_background_context_prompt",
        lambda: "default background rule",
    )
    monkeypatch.setattr(
        routes_directory_rules,
        "get_default_video_segment_prompt",
        lambda: "default segment prompt",
    )
    monkeypatch.setattr(
        routes_directory_rules,
        "get_default_video_final_summary_prompt",
        lambda: "default final prompt",
    )


def test_create_rule_saves_display_path_with_forward_slashes() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        rule = create_rule(_rule_create_payload("D:\\Photos\\School\\"), db=db)

    assert rule.path == "D:/Photos/School"


def test_list_rules_existing_only_hides_missing_directories(tmp_path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    existing = tmp_path / "existing"
    existing.mkdir()

    with SessionLocal() as db:
        create_rule(_rule_create_payload(str(existing)), db=db)
        create_rule(_rule_create_payload(str(tmp_path / "missing")), db=db)

        rules = list_rules(existing_only=True, db=db)

    assert [rule.path for rule in rules] == [str(existing).replace("\\", "/")]


def test_create_rule_replaces_stale_duplicate_rule() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        stale = create_rule(_rule_create_payload("Z:/Missing"), db=db)
        replacement = create_rule(
            DirectoryRuleCreate(
                path="Z:/Missing",
                recursive=False,
                vision_model="new-vision",
                summary_model="new-summary",
            ),
            db=db,
        )
        rules = list(db.scalars(select(DirectoryRule)).all())

    assert stale.id != replacement.id
    assert len(rules) == 1
    assert rules[0].vision_model == "new-vision"


def test_create_rule_autocompletes_missing_intermediate_rule_with_defaults(tmp_path, monkeypatch) -> None:
    _patch_intermediate_defaults(monkeypatch)
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    root = tmp_path / "A"
    target = root / "B" / "C"
    sibling = root / "Sibling"
    target.mkdir(parents=True)
    sibling.mkdir()

    with SessionLocal() as db:
        create_rule(
            DirectoryRuleCreate(
                path=str(root),
                recursive=True,
                vision_model="parent-vision",
                summary_model="parent-summary",
                custom_analysis_prompt="parent analysis",
                background_context="parent background",
            ),
            db=db,
        )
        child = create_rule(
            DirectoryRuleCreate(
                path=str(target),
                recursive=True,
                vision_model="target-vision",
                summary_model="target-summary",
                custom_analysis_prompt="target analysis",
                background_context="target background",
            ),
            db=db,
        )
        rules = list(db.scalars(select(DirectoryRule)).all())

    by_path = {rule.normalized_path: rule for rule in rules}
    intermediate = by_path[routes_directory_rules.normalize_path(root / "B")]
    assert set(by_path) == {
        routes_directory_rules.normalize_path(root),
        routes_directory_rules.normalize_path(root / "B"),
        routes_directory_rules.normalize_path(target),
    }
    assert routes_directory_rules.normalize_path(sibling) not in by_path
    assert intermediate.vision_model == "default-vision"
    assert intermediate.summary_model == "default-summary"
    assert intermediate.custom_analysis_prompt == "default analysis"
    assert intermediate.background_context == ""
    assert intermediate.background_context_prompt == "default background rule"
    assert intermediate.video_segment_prompt == "default segment prompt"
    assert intermediate.video_final_summary_prompt == "default final prompt"
    assert intermediate.recursive is False
    assert intermediate.frame_interval_seconds == 3
    assert child.vision_model == "target-vision"


def test_create_rule_does_not_overwrite_existing_intermediate_rule(tmp_path, monkeypatch) -> None:
    _patch_intermediate_defaults(monkeypatch)
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    root = tmp_path / "A"
    middle_path = root / "B"
    target = middle_path / "C"
    target.mkdir(parents=True)

    with SessionLocal() as db:
        create_rule(_rule_create_payload(str(root)), db=db)
        middle = create_rule(
            DirectoryRuleCreate(
                path=str(middle_path),
                recursive=True,
                vision_model="middle-vision",
                summary_model="middle-summary",
                custom_analysis_prompt="middle analysis",
            ),
            db=db,
        )
        create_rule(_rule_create_payload(str(target)), db=db)
        db.refresh(middle)
        rules = list(db.scalars(select(DirectoryRule)).all())

    assert len(rules) == 3
    assert middle.vision_model == "middle-vision"
    assert middle.summary_model == "middle-summary"
    assert middle.custom_analysis_prompt == "middle analysis"


def test_create_rule_without_existing_ancestor_does_not_autocomplete_intermediates(tmp_path, monkeypatch) -> None:
    _patch_intermediate_defaults(monkeypatch)
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    target = tmp_path / "A" / "B" / "C"
    target.mkdir(parents=True)

    with SessionLocal() as db:
        create_rule(_rule_create_payload(str(target)), db=db)
        rules = list(db.scalars(select(DirectoryRule)).all())

    assert [rule.normalized_path for rule in rules] == [routes_directory_rules.normalize_path(target)]


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


def test_autocompleted_intermediate_rule_cannot_enable_disabled_parent_scope(tmp_path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    root = tmp_path / "Photos"
    target = root / "School" / "Class"
    target.mkdir(parents=True)

    with SessionLocal() as db:
        disabled_parent = DirectoryRule(
            path=str(root),
            normalized_path=routes_directory_rules.normalize_path(root),
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

        child = create_rule(_rule_create_payload(str(target)), db=db)
        intermediate = db.scalar(
            select(DirectoryRule).where(
                DirectoryRule.normalized_path == routes_directory_rules.normalize_path(root / "School")
            )
        )

    assert intermediate is not None
    assert intermediate.enabled is False
    assert child.enabled is False
