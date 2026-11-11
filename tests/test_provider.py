import asyncio
import json

import pytest

from aegis_ai.models import ChatMessage
from aegis_ai.provider import OpenAICompatibleProvider, ProviderError


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, limit):
        return self.body


def test_provider_parses_tool_calls_and_sends_auth(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["authorization"] = request.get_header("Authorization")
        captured["body"] = json.loads(request.data)
        body = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "function": {
                                    "name": "calculator",
                                    "arguments": '{"expression":"19 * 23"}',
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4},
        }
        return FakeResponse(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = OpenAICompatibleProvider(
        "http://localhost:8000/v1",
        "test-model",
        api_key="test-secret",
        timeout_seconds=3,
    )

    result = asyncio.run(provider.complete([ChatMessage(role="user", content="calculate")], []))

    assert captured["url"] == "http://localhost:8000/v1/chat/completions"
    assert captured["timeout"] == 3
    assert captured["authorization"] == "Bearer test-secret"
    assert captured["body"]["model"] == "test-model"
    assert result.tool_calls[0].name == "calculator"
    assert result.tool_calls[0].arguments == {"expression": "19 * 23"}
    assert result.usage == {"prompt_tokens": 12, "completion_tokens": 4}


def test_provider_rejects_invalid_response(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: FakeResponse(b"not-json"),
    )
    provider = OpenAICompatibleProvider("http://localhost:8000/v1", "test-model")

    with pytest.raises(ProviderError, match="invalid JSON"):
        asyncio.run(provider.complete([ChatMessage(role="user", content="hello")], []))
