import asyncio
import json

import pytest
import httpx

from app.services import ollama_client as ollama_client_module
from app.services.ollama_client import OllamaClient, OllamaError, _parse_json_object, _raise_for_status_with_body


def test_parse_plain_json_object() -> None:
    assert _parse_json_object('{"title": "x"}') == {"title": "x"}


def test_parse_fenced_json_object() -> None:
    assert _parse_json_object('```json\n{"title": "x"}\n```') == {"title": "x"}


def test_parse_json_object_embedded_in_text() -> None:
    assert _parse_json_object('result:\n{"title": "x"}\nend') == {"title": "x"}


def test_parse_empty_json_response_fails() -> None:
    with pytest.raises(json.JSONDecodeError):
        _parse_json_object("")


def test_http_status_error_includes_full_response_body() -> None:
    request = httpx.Request("POST", "http://localhost:11434/api/generate")
    body = "model load failed\n" + ("x" * 2000)
    response = httpx.Response(500, request=request, text=body)

    with pytest.raises(OllamaError) as exc_info:
        _raise_for_status_with_body(response)

    message = str(exc_info.value)
    assert "Ollama returned HTTP 500" in message
    assert body in message


def test_generate_vision_json_uses_configured_image_system_prompt(monkeypatch) -> None:
    captured_payload = {}
    client = OllamaClient()

    monkeypatch.setattr(ollama_client_module, "_image_to_base64", lambda image_path, max_long_edge: "image-b64")
    monkeypatch.setattr(
        ollama_client_module,
        "get_default_analysis_system_prompt",
        lambda: "可配置图片 system prompt",
    )

    async def fake_generate_json(payload: dict) -> dict:
        captured_payload.update(payload)
        return {"title": "ok"}

    monkeypatch.setattr(client, "_generate_json", fake_generate_json)

    asyncio.run(
        client.generate_vision_json(
            model="vision-model",
            image_path="image.jpg",
            schema={"type": "object"},
            user_prompt="user prompt",
        )
    )

    assert captured_payload["system"] == "可配置图片 system prompt"
