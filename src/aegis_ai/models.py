"""Core immutable contracts shared by the runtime and provider adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class RiskLevel(StrEnum):
    READ_ONLY = "read_only"
    WRITE = "write"
    EXTERNAL = "external"
    DESTRUCTIVE = "destructive"


class Decision(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class RunStatus(StrEnum):
    COMPLETED = "completed"
    STEP_LIMIT = "step_limit"


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True, slots=True)
class ModelTurn:
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Mapping[str, int] = field(default_factory=dict)


ToolHandler = Callable[[Mapping[str, Any]], Any | Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: Mapping[str, Any]
    risk: RiskLevel
    handler: ToolHandler


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: str
    output: str
    status: RunStatus
    steps: int
    tool_calls: int


class ModelProvider(Protocol):
    async def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[Mapping[str, Any]],
    ) -> ModelTurn:
        """Return one assistant turn for the supplied conversation."""
