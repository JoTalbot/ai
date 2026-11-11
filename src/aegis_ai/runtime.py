"""Bounded agent loop with deterministic validation and policy enforcement."""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from aegis_ai.audit import AuditLog
from aegis_ai.models import (
    ChatMessage,
    Decision,
    ModelProvider,
    ModelTurn,
    RunResult,
    RunStatus,
    ToolCall,
    ToolSpec,
)
from aegis_ai.policy import SafePolicy
from aegis_ai.registry import ToolRegistry, validate_arguments


ApprovalHandler = Callable[[ToolSpec, Mapping[str, Any]], bool | Awaitable[bool]]
_DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant. Tool calls are requests, not permissions. "
    "Use only the tools supplied to you and explain when an action is blocked."
)
_TOOL_RESULT_TOO_LARGE = '{"error":"tool_result_exceeded_size_limit"}'
_MAX_MODEL_CONTENT_CHARS = 65_536
_MAX_TOOL_ARGUMENT_CHARS = 65_536
_MAX_TOOL_CALLS_PER_TURN = 128


class AgentRuntime:
    def __init__(
        self,
        provider: ModelProvider,
        tools: ToolRegistry,
        policy: SafePolicy | None = None,
        audit: AuditLog | None = None,
        approval_handler: ApprovalHandler | None = None,
        max_steps: int = 8,
        max_tool_calls: int = 32,
        max_tool_result_chars: int = 16_384,
        max_prompt_chars: int = 32_768,
        max_system_prompt_chars: int = 16_384,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls must be at least 1")
        if max_tool_result_chars < 128:
            raise ValueError("max_tool_result_chars must be at least 128")
        if max_prompt_chars < 1:
            raise ValueError("max_prompt_chars must be at least 1")
        if max_system_prompt_chars < 1:
            raise ValueError("max_system_prompt_chars must be at least 1")
        self.provider = provider
        self.tools = tools
        self.policy = policy or SafePolicy()
        self.audit = audit
        self.approval_handler = approval_handler
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.max_tool_result_chars = max_tool_result_chars
        self.max_prompt_chars = max_prompt_chars
        self.max_system_prompt_chars = max_system_prompt_chars

    async def run(
        self,
        prompt: str,
        system_prompt: str = _DEFAULT_SYSTEM_PROMPT,
    ) -> RunResult:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if len(prompt) > self.max_prompt_chars:
            raise ValueError("prompt exceeds max_prompt_chars")
        if not isinstance(system_prompt, str):
            raise ValueError("system_prompt must be a string")
        if len(system_prompt) > self.max_system_prompt_chars:
            raise ValueError("system_prompt exceeds max_system_prompt_chars")

        run_id = str(uuid.uuid4())
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=prompt),
        ]
        tool_count = 0
        self._record(
            run_id,
            "run_started",
            {"max_steps": self.max_steps, "max_tool_calls": self.max_tool_calls},
        )

        for step in range(1, self.max_steps + 1):
            try:
                turn = await self.provider.complete(messages, self.tools.schemas())
            except Exception as exc:
                self._record(run_id, "run_failed", {"step": step, "error_type": type(exc).__name__})
                raise

            try:
                _validate_model_turn(turn)
            except ValueError:
                self._record(
                    run_id,
                    "run_failed",
                    {"step": step, "error_type": "InvalidModelTurn"},
                )
                raise

            messages.append(
                ChatMessage(role="assistant", content=turn.content, tool_calls=turn.tool_calls)
            )
            self._record(
                run_id,
                "model_turn",
                {"step": step, "tool_call_count": len(turn.tool_calls)},
            )

            if not turn.tool_calls:
                output = turn.content or ""
                self._record(run_id, "run_completed", {"steps": step, "tool_calls": tool_count})
                return RunResult(run_id, output, RunStatus.COMPLETED, step, tool_count)

            for index, call in enumerate(turn.tool_calls):
                if tool_count >= self.max_tool_calls:
                    remaining = turn.tool_calls[index:]
                    self._record(
                        run_id,
                        "run_tool_call_limit",
                        {"step": step, "limit": self.max_tool_calls, "remaining": len(remaining)},
                    )
                    for pending in remaining:
                        messages.append(
                            _tool_message(
                                pending,
                                {"error": "tool_call_limit_reached", "executed": False},
                                self.max_tool_result_chars,
                            )
                        )
                    return RunResult(
                        run_id,
                        "",
                        RunStatus.TOOL_CALL_LIMIT,
                        step,
                        tool_count,
                    )
                tool_count += 1
                messages.append(await self._handle_tool(run_id, call, self.max_tool_result_chars))

        self._record(run_id, "run_step_limit", {"steps": self.max_steps, "tool_calls": tool_count})
        return RunResult(run_id, "", RunStatus.STEP_LIMIT, self.max_steps, tool_count)

    async def _handle_tool(
        self,
        run_id: str,
        call: ToolCall,
        max_result_chars: int,
    ) -> ChatMessage:
        tool = self.tools.get(call.name)
        if tool is None:
            self._record(
                run_id,
                "tool_blocked",
                {
                    "tool": call.name,
                    "decision": "unknown_tool",
                    "argument_keys": sorted(call.arguments),
                },
            )
            return _tool_message(call, {"error": "unknown_tool"}, max_result_chars)

        errors = validate_arguments(tool.parameters, call.arguments)
        if errors:
            self._record(
                run_id,
                "tool_rejected",
                {
                    "tool": tool.name,
                    "reason": "invalid_arguments",
                    "argument_keys": sorted(call.arguments),
                    "error_count": len(errors),
                },
            )
            return _tool_message(
                call,
                {"error": "invalid_arguments", "details": errors},
                max_result_chars,
            )

        decision = self.policy.decide(tool)
        if decision is Decision.DENY:
            self._record(
                run_id,
                "tool_blocked",
                {"tool": tool.name, "risk": tool.risk.value, "decision": decision.value},
            )
            return _tool_message(
                call,
                {"error": "blocked_by_policy", "decision": decision.value},
                max_result_chars,
            )

        if decision is Decision.REQUIRE_APPROVAL:
            approved = await self._request_approval(run_id, tool, call.arguments)
            if not approved:
                self._record(
                    run_id,
                    "tool_blocked",
                    {
                        "tool": tool.name,
                        "risk": tool.risk.value,
                        "decision": "approval_missing_or_denied",
                    },
                )
                return _tool_message(
                    call,
                    {"error": "approval_required", "executed": False},
                    max_result_chars,
                )

        self._record(
            run_id,
            "tool_execution_started",
            {
                "tool": tool.name,
                "risk": tool.risk.value,
                "argument_keys": sorted(call.arguments),
            },
        )
        try:
            result = await self.tools.invoke(tool, call.arguments)
        except Exception as exc:
            self._record(
                run_id,
                "tool_execution_failed",
                {"tool": tool.name, "error_type": type(exc).__name__},
            )
            return _tool_message(call, {"error": "tool_execution_failed"}, max_result_chars)

        self._record(run_id, "tool_execution_completed", {"tool": tool.name})
        return _tool_message(call, result, max_result_chars)

    async def _request_approval(
        self,
        run_id: str,
        tool: ToolSpec,
        arguments: Mapping[str, Any],
    ) -> bool:
        if self.approval_handler is None:
            return False
        try:
            if inspect.iscoroutinefunction(self.approval_handler):
                result = await self.approval_handler(tool, arguments)
            else:
                result = await asyncio.to_thread(self.approval_handler, tool, arguments)
            if inspect.isawaitable(result):
                result = await result
            return result is True
        except Exception as exc:
            self._record(
                run_id,
                "approval_callback_failed",
                {"tool": tool.name, "error_type": type(exc).__name__},
            )
            return False

    def _record(self, run_id: str, event: str, payload: Mapping[str, Any]) -> None:
        if self.audit is not None:
            self.audit.append(run_id, event, payload)


def _tool_message(call: ToolCall, result: Any, max_chars: int) -> ChatMessage:
    if isinstance(result, str):
        content = result
    else:
        try:
            content = json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            content = json.dumps({"error": "tool_return_value_not_serializable"})
    if len(content) > max_chars:
        content = _TOOL_RESULT_TOO_LARGE
    return ChatMessage(
        role="tool",
        name=call.name,
        tool_call_id=call.id,
        content=content,
    )


def _validate_model_turn(turn: ModelTurn) -> None:
    """Reject oversized or structurally invalid provider output before it enters the loop."""
    if not isinstance(turn, ModelTurn):
        raise ValueError("provider must return a ModelTurn")
    if turn.content is not None and (
        not isinstance(turn.content, str) or len(turn.content) > _MAX_MODEL_CONTENT_CHARS
    ):
        raise ValueError("provider content is invalid or exceeds the 65536-character limit")
    if not isinstance(turn.tool_calls, tuple):
        raise ValueError("provider tool_calls must be a tuple")
    if len(turn.tool_calls) > _MAX_TOOL_CALLS_PER_TURN:
        raise ValueError("provider returned too many tool calls in one turn")
    seen_ids: set[str] = set()
    total_argument_chars = 0
    for call in turn.tool_calls:
        if not isinstance(call, ToolCall):
            raise ValueError("provider returned an invalid tool call")
        if not isinstance(call.id, str) or not call.id or len(call.id) > 128:
            raise ValueError("tool call id must contain 1-128 characters")
        if call.id in seen_ids:
            raise ValueError("provider returned duplicate tool call ids")
        if not isinstance(call.name, str) or not call.name or len(call.name) > 64:
            raise ValueError("tool name must contain 1-64 characters")
        if not isinstance(call.arguments, Mapping) or any(
            not isinstance(key, str) for key in call.arguments
        ):
            raise ValueError("tool call arguments must be an object with string keys")
        try:
            encoded = json.dumps(
                call.arguments,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("tool call arguments must contain finite JSON values") from exc
        if len(encoded) > _MAX_TOOL_ARGUMENT_CHARS:
            raise ValueError("tool call arguments exceed the 65536-character limit")
        total_argument_chars += len(encoded)
        if total_argument_chars > 262_144:
            raise ValueError("tool arguments exceed the 262144-character per-turn limit")
        seen_ids.add(call.id)
