from dataclasses import dataclass

from app.services.rule_resolver import resolve_rule, rule_config_hash


@dataclass
class Rule:
    path: str
    normalized_path: str
    enabled: bool = True
    vision_model: str = "vision-a"
    summary_model: str = "summary-a"
    custom_analysis_prompt: str | None = None
    background_context: str | None = None
    video_frame_strategy: str = "hybrid"
    frame_interval_seconds: int = 5
    max_frames_per_video: int = 12
    analysis_detail: str = "normal"


def test_resolve_rule_uses_longest_prefix() -> None:
    root = Rule(path="D:/Photos", normalized_path="d:/photos")
    school = Rule(path="D:/Photos/School", normalized_path="d:/photos/school", vision_model="vision-b")
    resolved = resolve_rule("D:/Photos/School/CAS/a.jpg", [root, school])
    assert resolved is school


def test_rule_hash_changes_when_model_changes() -> None:
    before = Rule(path="D:/Photos", normalized_path="d:/photos")
    after = Rule(path="D:/Photos", normalized_path="d:/photos", vision_model="vision-b")
    assert rule_config_hash(before) != rule_config_hash(after)


def test_rule_hash_changes_when_prompt_changes() -> None:
    before = Rule(path="D:/Photos", normalized_path="d:/photos")
    after = Rule(
        path="D:/Photos",
        normalized_path="d:/photos",
        custom_analysis_prompt="重点描述拍摄目的。",
    )
    assert rule_config_hash(before) != rule_config_hash(after)
