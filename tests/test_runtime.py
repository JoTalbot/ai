import asyncio
import threading

from aegis_ai.builtins import calculator_tool
from aegis_ai.models import ModelTurn, RiskLevel, RunStatus, ToolCall, ToolSpec
from aegis_ai.policy import SafePolicy
from aegis_ai.registry import ToolRegistry
from aegis_ai.runtime import AgentRuntime


class ScriptedProvider:
    def __init__(self, *turns):
        self.turns = list(turns)
        self.messages = []

    async def complete(self, messages, tools):
        self.messages.append(list(messages))
        return self.turns.pop(0)


def test_read_only_tool_runs_and_returns_final_answer():
    provider = ScriptedProvider(
        ModelTurn(tool_calls=(ToolCall("call-1", "calculator", {"expression": "19 * 23"}),)),
        ModelTurn(content="The answer is 437."),
    )
    registry = ToolRegistry()
    registry.register(calculator_tool())

    result = asyncio.run(AgentRuntime(provider, registry).run("Calculate 19 times 23."))

    assert result.status is RunStatus.COMPLETED
    assert result.output == "The answer is 437."
    assert result.tool_calls == 1
    tool_messages = [message for message in provider.messages[1] if message.role == "tool"]
    assert '"result": 437' in tool_messages[0].content


def test_write_tool_fails_closed_without_approval():
    calls = []

    def mutate(arguments):
        calls.append(arguments["value"])
        return {"ok": True}

    provider = ScriptedProvider(
        ModelTurn(tool_calls=(ToolCall("call-1", "mutate", {"value": "new"}),)),
        ModelTurn(content="The change was not run."),
    )
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            "mutate",
            "Change a value",
            {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            RiskLevel.WRITE,
            mutate,
        )
    )

    result = asyncio.run(AgentRuntime(provider, registry).run("Change the value."))

    assert result.output == "The change was not run."
    assert calls == []
    assert "approval_required" in provider.messages[1][-1].content


def test_write_tool_runs_only_after_explicit_approval():
    calls = []

    async def approve(tool, arguments):
        return tool.name == "mutate" and arguments["value"] == "approved"

    provider = ScriptedProvider(
        ModelTurn(tool_calls=(ToolCall("call-1", "mutate", {"value": "approved"}),)),
        ModelTurn(content="Completed."),
    )
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            "mutate",
            "Change a value",
            {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            RiskLevel.WRITE,
            lambda arguments: calls.append(arguments["value"]),
        )
    )

    asyncio.run(AgentRuntime(provider, registry, approval_handler=approve).run("Change the value."))

    assert calls == ["approved"]


def test_invalid_arguments_never_reach_handler():
    calls = []
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            "read",
            "Read a record",
            {
                "type": "object",
                "properties": {"record_id": {"type": "integer"}},
                "required": ["record_id"],
                "additionalProperties": False,
            },
            RiskLevel.READ_ONLY,
            lambda arguments: calls.append(arguments),
        )
    )
    provider = ScriptedProvider(
        ModelTurn(tool_calls=(ToolCall("call-1", "read", {"record_id": "not-an-integer"}),)),
        ModelTurn(content="Rejected."),
    )

    asyncio.run(AgentRuntime(provider, registry).run("Read a record."))

    assert calls == []
    assert "invalid_arguments" in provider.messages[1][-1].content


def test_destructive_tool_is_denied_even_when_approval_callback_exists():
    calls = []
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            "wipe",
            "Delete all records",
            {"type": "object", "properties": {}, "additionalProperties": False},
            RiskLevel.DESTRUCTIVE,
            lambda arguments: calls.append(True),
        )
    )
    provider = ScriptedProvider(
        ModelTurn(tool_calls=(ToolCall("call-1", "wipe", {}),)),
        ModelTurn(content="Blocked."),
    )
    runtime = AgentRuntime(
        provider,
        registry,
        policy=SafePolicy(),
        approval_handler=lambda tool, arguments: True,
    )

    asyncio.run(runtime.run("Delete everything."))

    assert calls == []
    assert "blocked_by_policy" in provider.messages[1][-1].content


def test_agent_stops_at_step_limit():
    provider = ScriptedProvider(
        ModelTurn(tool_calls=(ToolCall("1", "calculator", {"expression": "1+1"}),)),
        ModelTurn(tool_calls=(ToolCall("2", "calculator", {"expression": "2+2"}),)),
    )
    registry = ToolRegistry()
    registry.register(calculator_tool())

    result = asyncio.run(AgentRuntime(provider, registry, max_steps=2).run("Keep calculating."))

    assert result.status is RunStatus.STEP_LIMIT
    assert result.steps == 2


def test_agent_stops_when_tool_call_limit_is_reached():
    calls = []
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            "read",
            "Read-only test tool",
            {"type": "object", "properties": {}, "additionalProperties": False},
            RiskLevel.READ_ONLY,
            lambda arguments: calls.append(True),
        )
    )
    provider = ScriptedProvider(
        ModelTurn(
            tool_calls=(
                ToolCall("one", "read", {}),
                ToolCall("two", "read", {}),
            )
        )
    )

    result = asyncio.run(
        AgentRuntime(provider, registry, max_tool_calls=1).run("Call the tool twice.")
    )

    assert result.status is RunStatus.TOOL_CALL_LIMIT
    assert result.tool_calls == 1
    assert calls == [True]


def test_failed_approval_callback_fails_closed():
    calls = []
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            "mutate",
            "Write a value",
            {"type": "object", "properties": {}, "additionalProperties": False},
            RiskLevel.WRITE,
            lambda arguments: calls.append(True),
        )
    )

    async def broken_approval(tool, arguments):
        raise RuntimeError("approval system unavailable")

    provider = ScriptedProvider(
        ModelTurn(tool_calls=(ToolCall("one", "mutate", {}),)),
        ModelTurn(content="No change was made."),
    )
    result = asyncio.run(
        AgentRuntime(provider, registry, approval_handler=broken_approval).run("Change it.")
    )

    assert result.output == "No change was made."
    assert calls == []


def test_oversized_tool_result_is_replaced_with_bounded_error():
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            "large",
            "Return a large string",
            {"type": "object", "properties": {}, "additionalProperties": False},
            RiskLevel.READ_ONLY,
            lambda arguments: "x" * 1000,
        )
    )
    provider = ScriptedProvider(
        ModelTurn(tool_calls=(ToolCall("large-1", "large", {}),)),
        ModelTurn(content="The result was safely bounded."),
    )
    runtime = AgentRuntime(provider, registry, max_tool_result_chars=128)

    asyncio.run(runtime.run("Return a large result."))

    tool_message = provider.messages[1][-1]
    assert tool_message.content == '{"error":"tool_result_exceeded_size_limit"}'
    assert len(tool_message.content) < 128


def test_runtime_rejects_oversized_prompt_before_provider_call():
    provider = ScriptedProvider(ModelTurn(content="unused"))
    runtime = AgentRuntime(provider, ToolRegistry(), max_prompt_chars=8)

    try:
        asyncio.run(runtime.run("x" * 9))
    except ValueError as exc:
        assert "max_prompt_chars" in str(exc)
    else:
        raise AssertionError("oversized prompt was accepted")
    assert provider.messages == []


def test_runtime_rejects_oversized_model_content():
    provider = ScriptedProvider(ModelTurn(content="x" * 65_537))
    runtime = AgentRuntime(provider, ToolRegistry())

    try:
        asyncio.run(runtime.run("hello"))
    except ValueError as exc:
        assert "65536" in str(exc)
    else:
        raise AssertionError("oversized model output was accepted")


def test_runtime_rejects_duplicate_tool_call_ids():
    provider = ScriptedProvider(
        ModelTurn(
            tool_calls=(
                ToolCall("duplicate", "calculator", {"expression": "1+1"}),
                ToolCall("duplicate", "calculator", {"expression": "2+2"}),
            )
        )
    )
    registry = ToolRegistry()
    registry.register(calculator_tool())

    try:
        asyncio.run(AgentRuntime(provider, registry).run("calculate"))
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate tool-call IDs were accepted")


def test_runtime_rejects_non_finite_tool_arguments():
    provider = ScriptedProvider(
        ModelTurn(tool_calls=(ToolCall("one", "calculator", {"expression": float("nan")}),))
    )
    registry = ToolRegistry()
    registry.register(calculator_tool())

    try:
        asyncio.run(AgentRuntime(provider, registry).run("calculate"))
    except ValueError as exc:
        assert "finite JSON" in str(exc)
    else:
        raise AssertionError("non-finite tool arguments were accepted")


def test_sync_tool_handler_runs_off_the_event_loop_thread():
    handler_thread = []
    loop_thread = []

    def sync_handler(arguments):
        handler_thread.append(threading.get_ident())
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            "thread_check",
            "Record the thread used for synchronous execution",
            {"type": "object", "properties": {}, "additionalProperties": False},
            RiskLevel.READ_ONLY,
            sync_handler,
        )
    )
    provider = ScriptedProvider(
        ModelTurn(tool_calls=(ToolCall("one", "thread_check", {}),)),
        ModelTurn(content="done"),
    )

    async def run():
        loop_thread.append(threading.get_ident())
        await AgentRuntime(provider, registry).run("check thread")

    asyncio.run(run())

    assert handler_thread
    assert loop_thread
    assert handler_thread[0] != loop_thread[0]
