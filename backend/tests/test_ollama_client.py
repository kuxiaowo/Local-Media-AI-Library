import json

import pytest

from app.services.ollama_client import _parse_json_object


def test_parse_plain_json_object() -> None:
    assert _parse_json_object('{"title": "x"}') == {"title": "x"}


def test_parse_fenced_json_object() -> None:
    assert _parse_json_object('```json\n{"title": "x"}\n```') == {"title": "x"}


def test_parse_json_object_embedded_in_text() -> None:
    assert _parse_json_object('result:\n{"title": "x"}\nend') == {"title": "x"}


def test_parse_empty_json_response_fails() -> None:
    with pytest.raises(json.JSONDecodeError):
        _parse_json_object("")
