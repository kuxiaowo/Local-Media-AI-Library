from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.config import Settings, get_settings


DEEPSEEK_JSON_SYSTEM_PROMPT = (
    "所有用户可见文本使用简体中文。只返回一个有效 JSON object，不要输出 Markdown、解释或额外文本。"
)


class DeepSeekError(RuntimeError):
    pass


class DeepSeekClient:
    def __init__(self, settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings or get_settings()
        self.base_url = self.settings.deepseek_base_url.rstrip("/")
        self.timeout = self.settings.deepseek_timeout_seconds
        self._transport = transport

    async def generate_text_json(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict[str, Any],
        system_prompt: str | None = None,
    ) -> dict:
        api_key = self.settings.deepseek_api_key.strip()
        if not api_key:
            raise DeepSeekError("DeepSeek API key is not configured")

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt or DEEPSEEK_JSON_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"{prompt}\n\n"
                        "Return one JSON object that matches this JSON Schema. "
                        "Do not wrap it in Markdown.\n"
                        f"{json.dumps(schema, ensure_ascii=False)}"
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "stream": False,
            "temperature": 0,
            "thinking": {"type": "disabled"},
        }
        async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport, trust_env=False) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            _raise_for_status_with_body(response)

        response_payload = response.json()
        choices = response_payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise DeepSeekError("DeepSeek response did not include choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise DeepSeekError("DeepSeek response did not include a message")
        text = message.get("content") or ""
        try:
            parsed = _parse_json_object(text)
        except json.JSONDecodeError as exc:
            raise DeepSeekError(f"DeepSeek returned invalid JSON: {exc}; raw response:\n{text}") from exc
        if not isinstance(parsed, dict):
            raise DeepSeekError("DeepSeek JSON response is not an object")
        return parsed


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
        raise DeepSeekError(
            f"DeepSeek returned HTTP {response.status_code} from {response.request.method} "
            f"{response.request.url}\n{body}"
        ) from exc
