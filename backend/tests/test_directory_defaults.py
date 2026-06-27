from __future__ import annotations

from types import SimpleNamespace

from app.models.schemas import DirectoryRuleDefaults
from app.services import directory_defaults


def _patch_fallbacks(tmp_path, monkeypatch) -> None:
    settings = SimpleNamespace(
        cache_dir=tmp_path / "cache",
        default_vision_model="vision-default",
        default_summary_model="summary-default",
        default_frame_interval_seconds=7,
        default_max_frames_per_video=21,
        default_video_frame_max_width=1024,
        default_video_frame_max_height=720,
        default_video_batch_size=4,
        default_video_batch_overlap=2,
    )
    monkeypatch.setattr(directory_defaults, "get_settings", lambda: settings)


def test_directory_rule_defaults_use_runtime_fallbacks(tmp_path, monkeypatch) -> None:
    _patch_fallbacks(tmp_path, monkeypatch)

    defaults = directory_defaults.get_directory_rule_defaults()

    assert defaults.vision_model == "vision-default"
    assert defaults.summary_model == "summary-default"
    assert defaults.frame_interval_seconds == 7
    assert defaults.max_frames_per_video == 21
    assert defaults.video_frame_max_width == 1024
    assert defaults.video_frame_max_height == 720
    assert defaults.video_batch_size == 4
    assert defaults.video_batch_overlap == 2


def test_directory_rule_defaults_are_saved_and_loaded_without_restart(tmp_path, monkeypatch) -> None:
    _patch_fallbacks(tmp_path, monkeypatch)

    payload = DirectoryRuleDefaults(
        recursive=False,
        vision_model="vision-custom",
        summary_model="summary-custom",
        video_frame_strategy="fixed_interval",
        frame_interval_seconds=3,
        max_frames_per_video=9,
        video_frame_max_width=640,
        video_frame_max_height=None,
        video_batch_size=2,
        video_batch_overlap=0,
        analysis_detail="brief",
        enabled=False,
    )

    saved = directory_defaults.update_directory_rule_defaults(payload)
    loaded = directory_defaults.get_directory_rule_defaults()

    assert saved == payload
    assert loaded == payload


def test_directory_rule_defaults_ignore_legacy_prompt_fields(tmp_path, monkeypatch) -> None:
    _patch_fallbacks(tmp_path, monkeypatch)
    path = directory_defaults._defaults_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """{
  "vision_model": "vision-custom",
  "summary_model": "summary-custom",
  "custom_analysis_prompt": "legacy image prompt",
  "background_context": "legacy background",
  "background_context_prompt": "legacy background prompt",
  "video_segment_prompt": "legacy segment prompt",
  "video_final_summary_prompt": "legacy final prompt"
}
""",
        encoding="utf-8",
    )

    loaded = directory_defaults.get_directory_rule_defaults()

    assert loaded.vision_model == "vision-custom"
    assert loaded.summary_model == "summary-custom"
    assert not hasattr(loaded, "custom_analysis_prompt")
    assert not hasattr(loaded, "background_context")


def test_reset_directory_rule_defaults_removes_saved_file(tmp_path, monkeypatch) -> None:
    _patch_fallbacks(tmp_path, monkeypatch)
    payload = directory_defaults.get_directory_rule_defaults().model_copy(update={"vision_model": "vision-custom"})
    directory_defaults.update_directory_rule_defaults(payload)

    reset = directory_defaults.reset_directory_rule_defaults()

    assert reset.vision_model == "vision-default"
    assert not directory_defaults._defaults_path().exists()
