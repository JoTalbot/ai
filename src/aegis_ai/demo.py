"""Offline, deterministic demonstration of a tool round-trip."""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from aegis_ai.audit import AuditLog
from aegis_ai.builtins import calculator_tool
from aegis_ai.models import ChatMessage, ModelTurn, ToolCall
from aegis_ai.registry import ToolRegistry
from aegis_ai.runtime import AgentRuntime


class _DemoProvider:
    def __init__(self) -> None:
        self._turn = 0

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[Mapping[str, Any]],
    ) -> ModelTurn:
        self._turn += 1
        if self._turn == 1:
            return ModelTurn(
                tool_calls=(
                    ToolCall(
                        id="demo-call-1",
                        name="calculator",
                        arguments={"expression": "19 * 23"},
                    ),
                )
            )
        return ModelTurn(
            content="19 × 23 = 437. The calculator ran locally; no network call was made."
        )


def run_demo() -> int:
    tools = ToolRegistry()
    tools.register(calculator_tool())
    with tempfile.TemporaryDirectory(prefix="aegis-demo-") as temporary:
        runtime = AgentRuntime(
            provider=_DemoProvider(),
            tools=tools,
            audit=AuditLog(Path(temporary) / "audit.db"),
        )
        result = asyncio.run(runtime.run("Calculate 19 multiplied by 23."))
        print(result.output)
        print(f"Run: {result.run_id} | steps: {result.steps} | tool calls: {result.tool_calls}")
    return 0
