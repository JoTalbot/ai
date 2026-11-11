# Aegis AI Runtime

**A safe-by-default, provider-agnostic runtime for tool-using AI agents.**

Aegis gives an agent a small, explicit execution boundary: a model can propose tool calls, but the runtime validates every argument, applies a risk policy, asks for human approval before side effects, and records a tamper-evident audit trail.

It is designed to work with local model gateways and hosted OpenAI-compatible APIs without binding the core runtime to one vendor.

> **Status:** early release candidate (0.3.0). The core is intentionally small and conservative. Review the security model before connecting tools that can change external state.

## Why Aegis

Most agent demos put model output and real-world actions in the same loop. Aegis separates them.

- **Provider-neutral:** an OpenAI-compatible adapter is included; the runtime depends only on a small protocol.
- **Safe by default:** read-only tools may run; write and external tools require an explicit approval callback; destructive tools are denied by default.
- **Validated tool inputs:** a dependency-free JSON Schema subset rejects missing, mistyped, and unexpected arguments before a handler runs.
- **Auditable:** events are written to SQLite with a SHA-256 hash chain. Prompts, model responses, tool outputs, and argument values are not stored by default.
- **Independent audit checkpoints:** HMAC-SHA256 checkpoints let an operator detect rollback or tail truncation when the checkpoint and signing key are retained outside the runtime host.
- **Local-first:** point the provider at a local OpenAI-compatible gateway. No account or vendor SDK is required by the core.
- **Small attack surface:** no shell, browser, filesystem, or network tools are registered automatically.

## Quick start

Requires Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
aegis demo
```

The demo uses a scripted model response and the built-in calculator. It does not make a network request or need an API key.

## Connect a model

Set the endpoint and model for any OpenAI Chat Completions-compatible service:

```bash
export AEGIS_BASE_URL="http://127.0.0.1:8000/v1"
export AEGIS_MODEL="your-model"
# Set this only when the endpoint requires a key:
export AEGIS_API_KEY="..."
```

Then run:

```bash
aegis doctor
aegis run "Calculate the compound growth of 1000 at 5% for 3 years." --max-steps 8 --max-tool-calls 32
aegis audit verify
```

The default audit database is `~/.aegis/audit.db`. Override it with `AEGIS_AUDIT_DB` or `--audit-db`. The API key is read from the environment and is never printed by `doctor`.

## Use it as a library

```python
import asyncio

from aegis_ai import AgentRuntime, AuditLog, OpenAICompatibleProvider
from aegis_ai.builtins import calculator_tool
from aegis_ai.config import Settings
from aegis_ai.policy import SafePolicy
from aegis_ai.registry import ToolRegistry


async def main() -> None:
    settings = Settings.from_env()
    provider = OpenAICompatibleProvider(
        base_url=settings.base_url,
        model=settings.model,
        api_key=settings.api_key,
        timeout_seconds=settings.timeout_seconds,
    )
    tools = ToolRegistry()
    tools.register(calculator_tool())

    runtime = AgentRuntime(
        provider=provider,
        tools=tools,
        policy=SafePolicy(),
        audit=AuditLog(settings.audit_db),
        max_steps=8,
    )
    result = await runtime.run("What is 19 * 23?")
    print(result.output)


asyncio.run(main())
```

### Register a tool

A tool must declare its name, JSON-like input schema, risk level, and handler:

```python
from aegis_ai.models import RiskLevel, ToolSpec


async def lookup_order(arguments):
    return {"status": "found", "order_id": arguments["order_id"]}


order_tool = ToolSpec(
    name="lookup_order",
    description="Look up an order without changing it.",
    parameters={
        "type": "object",
        "properties": {"order_id": {"type": "string", "minLength": 1}},
        "required": ["order_id"],
        "additionalProperties": False,
    },
    risk=RiskLevel.READ_ONLY,
    handler=lookup_order,
)
```

For tools that write data, choose `RiskLevel.WRITE` or `RiskLevel.EXTERNAL`. Aegis will not run them unless an approval callback is supplied and returns `True`. `RiskLevel.DESTRUCTIVE` is denied by the default policy. Do not label a side-effecting tool as read-only.

The default runtime limits each run to eight model turns, 32 tool calls, and 16,384 characters per tool result. The CLI exposes `--max-steps` and `--max-tool-calls`; library hosts can also configure `max_tool_result_chars`.

## Safety model

| Risk class | Default decision | Meaning |
|---|---|---|
| `read_only` | Allow | No external state change |
| `write` | Require approval | Changes local or business data |
| `external` | Require approval | Sends data or triggers an external service |
| `destructive` | Deny | Deletes, irreversibly changes, or performs high-impact actions |

Approval is a host-application responsibility. Aegis does not treat model text such as "the user approved this" as authorization. The host must provide an approval callback based on a real, trusted interaction.

The included CLI does not register side-effecting tools. A custom host may provide an interactive approval callback; non-interactive runs fail closed.

## Audit trail and signed checkpoints

Each audit event contains a timestamp, run identifier, event type, a minimal metadata payload, the previous event hash, and its own hash. Verification detects changes to retained events and broken links in the chain.

For stronger rollback detection, create a signed checkpoint and store it outside the machine hosting the database:

```bash
# Supply a random secret from a secret manager, not a checked-in file.
export AEGIS_AUDIT_CHECKPOINT_KEY="<at-least-32-random-UTF-8-bytes>"
aegis audit checkpoint --output /secure/external/aegis-checkpoint.json
aegis audit verify --checkpoint /secure/external/aegis-checkpoint.json
```

The key must contain at least 32 UTF-8 bytes. Keep the checkpoint and key outside the audit host. HMAC is a shared-secret integrity check, not a digital signature or proof to third parties. A local chain without an external checkpoint cannot detect removal of its final rows.

## Configuration

| Environment variable | Default | Purpose |
|---|---|---|
| `AEGIS_BASE_URL` | `http://127.0.0.1:8000/v1` | OpenAI-compatible API base URL |
| `AEGIS_MODEL` | `local-model` | Model identifier |
| `AEGIS_API_KEY` | unset | Optional API key |
| `AEGIS_TIMEOUT_SECONDS` | `60` | HTTP timeout |
| `AEGIS_AUDIT_DB` | `~/.aegis/audit.db` | SQLite audit database |
| `AEGIS_AUDIT_CHECKPOINT_KEY` | unset | External checkpoint HMAC key (32+ UTF-8 bytes) |

## Development

```bash
python -m pip install -e ".[dev]"
ruff check .
ruff format --check .
pytest
```

CI runs lint, formatting, compilation, and tests across supported Python versions. A separate daily workflow audits installed dependencies with `pip-audit`. CodeQL analyzes Python changes; Dependabot proposes weekly dependency and GitHub Actions updates. Updates are reviewed and are not automatically merged.

## Project principles

1. The model proposes; deterministic code disposes.
2. Missing, malformed, or ambiguous input must not become permission.
3. Side effects require explicit policy and trusted approval.
4. Audit data should be useful without becoming a second store of sensitive conversations.
5. Integrations are opt-in. Nothing silently connects to a business system.

See [SECURITY.md](SECURITY.md), [docs/architecture.md](docs/architecture.md), and [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).
