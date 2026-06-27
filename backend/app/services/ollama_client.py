from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from app.config import Settings, get_settings
from app.prompts.image_analysis import IMAGE_ANALYSIS_SYSTEM_PROMPT, build_image_analysis_user_prompt
from app.services.prompt_settings import (
    get_default_analysis_prompt,
    get_default_analysis_system_prompt,
    get_default_background_context_prompt,
)

JSON_SYSTEM_PROMPT = "所有用户可见文本使用简体中文。只返回符合 schema 的 JSON，不输出解释。"


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.base_url = self.settings.ollama_base_url.rstrip("/")
        self.timeout = self.settings.ollama_timeout_seconds

    async def health_check(self) -> tuple[bool, str | None]:
        try:
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                _raise_for_status_with_body(response)
            return True, None
        except Exception as exc:
            return False, str(exc)

    async def list_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.get(f"{self.base_url}/api/tags")
            _raise_for_status_with_body(response)
        payload = response.json()
        return sorted(model.get("name", "") for model in payload.get("models", []) if model.get("name"))

    async def generate_vision_json(
        self,
        *,
        model: str,
        image_path: str,
        schema: dict[str, Any],
        system_prompt: str | None = None,
        user_prompt: str | None = None,
        custom_analysis_prompt: str | None = None,
        background_context: str | None = None,
        background_context_prompt: str | None = None,
        source_filename: str | None = None,
    ) -> dict:
        image_b64 = _image_to_base64(image_path, self.settings.max_image_long_edge)
        payload = {
            "model": model,
            "system": system_prompt or get_default_analysis_system_prompt() or IMAGE_ANALYSIS_SYSTEM_PROMPT,
            "prompt": user_prompt
            or build_image_analysis_user_prompt(
                custom_analysis_prompt=custom_analysis_prompt,
                background_context=background_context,
                background_context_prompt=background_context_prompt,
                source_filename=source_filename,
                default_analysis_prompt=get_default_analysis_prompt(),
                default_background_context_prompt=get_default_background_context_prompt(),
            ),
            "images": [image_b64],
            "format": schema,
            "stream": False,
            "think": False,
            "keep_alive": self.settings.ollama_keep_alive,
            "options": {"num_ctx": self.settings.ollama_num_ctx},
        }
        return await self._generate_json(payload)

    async def generate_vision_batch_json(
        self,
        *,
        model: str,
        image_paths: list[str],
        prompt: str,
        schema: dict[str, Any],
        system_prompt: str | None = None,
    ) -> dict:
        if not image_paths:
            raise OllamaError("Vision batch request requires at least one image")
        images = [_image_to_base64(image_path, self.settings.max_image_long_edge) for image_path in image_paths]
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt or JSON_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": prompt,
                    "images": images,
                },
            ],
            "format": schema,
            "stream": False,
            "think": False,
            "keep_alive": self.settings.ollama_keep_alive,
            "options": {"num_ctx": self.settings.ollama_num_ctx},
        }
        return await self._generate_chat_json(payload)

    async def generate_text_json(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict[str, Any],
        system_prompt: str | None = None,
    ) -> dict:
        payload = {
            "model": model,
            "system": system_prompt or JSON_SYSTEM_PROMPT,
            "prompt": prompt,
            "format": schema,
            "stream": False,
            "think": False,
            "keep_alive": self.settings.ollama_keep_alive,
            "options": {"num_ctx": self.settings.ollama_num_ctx},
        }
        return await self._generate_json(payload)

    async def embed_text(self, *, model: str, text: str) -> list[float]:
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            response = await client.post(
                f"{self.base_url}/api/embed",
                json={"model": model, "input": text, "keep_alive": self.settings.ollama_keep_alive},
            )
            if response.status_code == 404:
                response = await client.post(
                    f"{self.base_url}/api/embeddings",
                    json={"model": model, "prompt": text, "keep_alive": self.settings.ollama_keep_alive},
                )
            _raise_for_status_with_body(response)
        payload = response.json()
        if "embeddings" in payload and payload["embeddings"]:
            return [float(v) for v in payload["embeddings"][0]]
        if "embedding" in payload:
            return [float(v) for v in payload["embedding"]]
        raise OllamaError("Ollama embedding response did not include an embedding")

    async def _generate_json(self, payload: dict[str, Any]) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            response = await client.post(f"{self.base_url}/api/generate", json=payload)
            _raise_for_status_with_body(response)
        response_payload = response.json()
        text = response_payload.get("response") or response_payload.get("thinking") or ""
        try:
            parsed = _parse_json_object(text)
        except json.JSONDecodeError as exc:
            raise OllamaError(f"Ollama returned invalid JSON: {exc}; raw response:\n{text}") from exc
        if not isinstance(parsed, dict):
            raise OllamaError("Ollama JSON response is not an object")
        return parsed

    async def _generate_chat_json(self, payload: dict[str, Any]) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            response = await client.post(f"{self.base_url}/api/chat", json=payload)
            _raise_for_status_with_body(response)
        response_payload = response.json()
        message = response_payload.get("message") or {}
        text = message.get("content") or response_payload.get("response") or response_payload.get("thinking") or ""
        try:
            parsed = _parse_json_object(text)
        except json.JSONDecodeError as exc:
            raise OllamaError(f"Ollama returned invalid JSON: {exc}; raw response:\n{text}") from exc
        if not isinstance(parsed, dict):
            raise OllamaError("Ollama JSON response is not an object")
        return parsed


def _image_to_base64(image_path: str, max_long_edge: int) -> str:
    path = Path(image_path)
    with Image.open(path) as image:
        image = image.convert("RGB")
        longest = max(image.size)
        if longest > max_long_edge:
            scale = max_long_edge / longest
            image = image.resize((int(image.width * scale), int(image.height * scale)))
        from io import BytesIO

        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=90)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise json.JSONDecodeError("empty JSON response", text, 0)

    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        parsed = json.loads(fenced.group(1))
        if isinstance(parsed, dict):
            return parsed

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        parsed = json.loads(stripped[start : end + 1])
        if isinstance(parsed, dict):
            return parsed

    raise json.JSONDecodeError("JSON object not found", text, 0)


def _raise_for_status_with_body(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = response.text
        raise OllamaError(
            f"Ollama returned HTTP {response.status_code} from {response.request.method} "
            f"{response.request.url}\n{body}"
        ) from exc
