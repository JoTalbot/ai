"""OpenAI-compatible Chat Completions adapter using only the standard library."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urljoin

from aegis_ai.models import ChatMessage, ModelTurn, ToolCall


class ProviderError(RuntimeError):
    pass


class OpenAICompatibleProvider:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[Mapping[str, Any]],
    ) -> ModelTurn:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [_message_payload(message) for message in messages],
        }
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"
        return await asyncio.to_thread(self._send, payload)

    def _send(self, payload: Mapping[str, Any]) -> ModelTurn:
        url = urljoin(self.base_url, "chat/completions")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "aegis-ai-runtime/0.1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            detail = exc.read(512).decode("utf-8", errors="replace")
            raise ProviderError(f"provider returned HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(f"could not reach model provider: {exc}") from exc

        if len(raw) > 8 * 1024 * 1024:
            raise ProviderError("provider response exceeded the 8 MiB safety limit")
        try:
            data = json.loads(raw)
            choice = data["choices"][0]["message"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("provider returned an invalid Chat Completions response") from exc

        content = choice.get("content")
        if content is not None and not isinstance(content, str):
            raise ProviderError("provider message content must be a string or null")

        calls: list[ToolCall] = []
        for index, item in enumerate(choice.get("tool_calls") or []):
            function = item.get("function") or {}
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError) as exc:
                raise ProviderError("provider returned invalid tool argument JSON") from exc
            if not isinstance(arguments, dict):
                raise ProviderError("tool call arguments must decode to a JSON object")
            calls.append(
                ToolCall(
                    id=str(item.get("id") or f"call-{index}"),
                    name=str(function.get("name") or ""),
                    arguments=arguments,
                )
            )

        raw_usage = data.get("usage") or {}
        usage = {
            key: value
            for key, value in raw_usage.items()
            if isinstance(value, int) and not isinstance(value, bool)
        }
        return ModelTurn(content=content, tool_calls=tuple(calls), usage=usage)


def _message_payload(message: ChatMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.name:
        payload["name"] = message.name
    if message.tool_call_id:
        payload["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in message.tool_calls
        ]
    return payload
