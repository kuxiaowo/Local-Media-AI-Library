from __future__ import annotations

from types import SimpleNamespace

from app.api.routes_settings import RuntimeSettings, update_runtime_settings
from app.config import get_settings


def test_update_runtime_settings_preserves_existing_deepseek_key_when_blank(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=old-key\n", encoding="utf-8")
    get_settings.cache_clear()

    response = update_runtime_settings(
        RuntimeSettings(
            ai_search_provider="deepseek",
            default_embedding_model="nomic-embed-text",
            default_ai_search_model="qwen3:8b",
            ai_search_max_turns=8,
            deepseek_base_url="https://api.deepseek.com/",
            deepseek_model="deepseek-v4-flash",
            deepseek_api_key="",
            max_image_long_edge=1280,
            scan_worker_concurrency=1,
            metadata_worker_concurrency=6,
            vision_worker_concurrency=1,
            embedding_worker_concurrency=1,
        ),
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())),
    )

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY=old-key" in env_text
    assert "AI_SEARCH_PROVIDER=deepseek" in env_text
    assert "DEEPSEEK_BASE_URL=https://api.deepseek.com" in env_text
    assert response.deepseek_api_key == ""
    assert response.deepseek_api_key_set is True
    get_settings.cache_clear()
