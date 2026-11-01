from __future__ import annotations

from typing import Protocol

from app.config import Settings, get_settings
from app.services.deepseek_client import DeepSeekClient
from app.services.ollama_client import OllamaClient


class TextJsonClient(Protocol):
    async def generate_text_json(self, **kwargs):
        ...


def ai_search_client(settings: Settings | None = None) -> TextJsonClient:
    resolved = settings or get_settings()
    provider = getattr(resolved, "ai_search_provider", "ollama").strip().lower()
    if provider == "deepseek":
        return DeepSeekClient(resolved)
    return OllamaClient(resolved)


def ai_search_model_name(settings: Settings | None = None) -> str:
    resolved = settings or get_settings()
    provider = getattr(resolved, "ai_search_provider", "ollama").strip().lower()
    if provider == "deepseek":
        return getattr(resolved, "deepseek_model", "").strip() or "deepseek-v4-flash"
    return resolved.default_ai_search_model.strip() or resolved.default_summary_model
