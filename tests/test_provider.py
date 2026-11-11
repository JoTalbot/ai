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


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://user:password@localhost/v1",
        "http://localhost/v1?token=secret",
        "http://localhost/v1#fragment",
        "http://localhost:invalid/v1",
    ],
)
def test_provider_rejects_unsafe_or_malformed_base_urls(url):
    with pytest.raises(ValueError):
        OpenAICompatibleProvider(url, "test-model")


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_provider_rejects_invalid_timeout(timeout):
    with pytest.raises(ValueError, match="timeout_seconds"):
        OpenAICompatibleProvider("http://localhost/v1", "test-model", timeout_seconds=timeout)


def test_provider_rejects_duplicate_tool_call_ids(monkeypatch):
    body = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {"id": "same", "function": {"name": "read", "arguments": "{}"}},
                        {"id": "same", "function": {"name": "read", "arguments": "{}"}},
                    ],
                }
            }
        ]
    }
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: FakeResponse(json.dumps(body).encode("utf-8")),
    )
    provider = OpenAICompatibleProvider("http://localhost/v1", "test-model")

    with pytest.raises(ProviderError, match="duplicate"):
        asyncio.run(provider.complete([ChatMessage(role="user", content="hello")], []))


def test_provider_rejects_empty_tool_name(monkeypatch):
    body = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {"id": "call-1", "function": {"name": " ", "arguments": "{}"}}
                    ]
                }
            }
        ]
    }
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: FakeResponse(json.dumps(body).encode("utf-8")),
    )
    provider = OpenAICompatibleProvider("http://localhost/v1", "test-model")

    with pytest.raises(ProviderError, match="empty tool name"):
        asyncio.run(provider.complete([ChatMessage(role="user", content="hello")], []))


def test_provider_rejects_oversized_response(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: FakeResponse(b"x" * (8 * 1024 * 1024 + 1)),
    )
    provider = OpenAICompatibleProvider("http://localhost/v1", "test-model")

    with pytest.raises(ProviderError, match="8 MiB"):
        asyncio.run(provider.complete([ChatMessage(role="user", content="hello")], []))
