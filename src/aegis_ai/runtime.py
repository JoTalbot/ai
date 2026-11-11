"""Bounded agent loop with deterministic validation and policy enforcement."""

from __future__ import annotations

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
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls must be at least 1")
        if max_tool_result_chars < 128:
            raise ValueError("max_tool_result_chars must be at least 128")
        self.provider = provider
        self.tools = tools
        self.policy = policy or SafePolicy()
        self.audit = audit
        self.approval_handler = approval_handler
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.max_tool_result_chars = max_tool_result_chars

    async def run(
        self,
        prompt: str,
        system_prompt: str = _DEFAULT_SYSTEM_PROMPT,
    ) -> RunResult:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")

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
                messages.append(
                    await self._handle_tool(run_id, call, self.max_tool_result_chars)
                )

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
            result = self.approval_handler(tool, arguments)
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
