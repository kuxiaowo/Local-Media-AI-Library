from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.services.deepseek_client import DeepSeekClient, DeepSeekError


def test_deepseek_generate_text_json_posts_openai_compatible_payload() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": '{"response_mode": "answer"}'}}]},
        )

    client = DeepSeekClient(
        SimpleNamespace(
            deepseek_base_url="https://api.deepseek.com",
            deepseek_timeout_seconds=180,
            deepseek_api_key="test-key",
        ),
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(
        client.generate_text_json(
            model="deepseek-v4-flash",
            prompt="Return JSON",
            schema={"type": "object"},
            system_prompt="System",
        )
    )

    assert result == {"response_mode": "answer"}
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["authorization"] == "Bearer test-key"
    assert captured["payload"]["model"] == "deepseek-v4-flash"
    assert captured["payload"]["messages"][0] == {"role": "system", "content": "System"}
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert "JSON Schema" in captured["payload"]["messages"][1]["content"]


def test_deepseek_generate_text_json_requires_api_key() -> None:
    client = DeepSeekClient(
        SimpleNamespace(
            deepseek_base_url="https://api.deepseek.com",
            deepseek_timeout_seconds=180,
            deepseek_api_key="",
        ),
        transport=httpx.MockTransport(lambda request: httpx.Response(500, request=request)),
    )

    with pytest.raises(DeepSeekError, match="API key"):
        asyncio.run(
            client.generate_text_json(model="deepseek-v4-flash", prompt="x", schema={"type": "object"})
        )
