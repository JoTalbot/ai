# Architecture

Aegis is split into five small boundaries. The model is deliberately not the authority for any of them.

## Request path

1. The host submits a user prompt to AgentRuntime.
2. AgentRuntime sends the conversation and registered tool schemas to a ModelProvider.
3. The provider returns assistant text and/or structured tool calls.
4. ToolRegistry resolves the name and validates the arguments against the registered schema.
5. SafePolicy classifies the tool by its declared risk. A write or external action is blocked unless the host supplies an approval callback and that callback approves. Destructive actions are denied by default.
6. Only after validation and policy approval does the runtime invoke the handler.
7. The runtime appends bounded tool results to the in-memory conversation and asks the provider for the next turn.
8. AuditLog stores minimal metadata about each transition and normalizes sensitive metadata keys before redaction.

## Modules

- models.py: immutable messages, tool contracts, provider protocol types, and run results.
- provider.py: OpenAI-compatible HTTP adapter. No vendor SDK is required.
- registry.py: tool registration, schema export, argument validation, and invocation. Synchronous handlers run in a worker thread so they do not block the event loop.
- policy.py: deterministic risk decisions.
- runtime.py: bounded agent loop, tool-call budget, result-size cap, and approval boundary.
- audit.py: SQLite event journal, hash-chain verification, and HMAC-signed checkpoints.
- builtins.py: a safe, bounded calculator demonstration.
- cli.py: local developer interface.

## Trust boundaries

The model output is untrusted input. Tool names, argument JSON, and claims of user consent are never accepted as authority. Registered handlers are trusted code and run with host process permissions. The host application is responsible for authentic approval and operating-system isolation.

Provider endpoint URLs are validated before use. This protects against malformed configuration and accidentally embedded credentials; it does not replace outbound network policy. Deployments should restrict egress to explicitly approved model endpoints.

Tool results are bounded before being returned to the model. This prevents a handler from accidentally flooding the context with an unbounded response. It is not a substitute for validating the semantic content returned by a tool.

## Failure behavior

- Invalid tool arguments never reach a handler.
- Unknown tools are returned to the model as failed tool results.
- Missing approval fails closed.
- Provider failures are recorded by error type and re-raised.
- The loop stops after max_steps or max_tool_calls.
- Tool results exceeding max_tool_result_chars are replaced with a fixed error response.
- Audit write failures propagate; the runtime does not silently claim an event was recorded.

## Audit integrity and independent checkpoints

Each row hashes a canonical JSON representation of its event fields plus the previous row hash. Verification checks both the previous-hash link and the event hash. This detects edits, deletions, reordering, and broken links among retained rows.

A signed checkpoint captures the event count, final row ID, and final event hash. It is authenticated with HMAC-SHA256 using a key of at least 32 bytes. Keep both the checkpoint and the key outside the runtime host, ideally in separate access-controlled systems. A checkpoint detects rollback or tail truncation relative to the retained snapshot. It does not protect against an attacker who can replace both the database and the external checkpoint, steal the signing key, or alter the runtime before a checkpoint is created. HMAC provides shared-secret authenticity, not public verifiability or non-repudiation.

Create and verify checkpoints:

```bash
export AEGIS_AUDIT_CHECKPOINT_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
aegis audit checkpoint --output /secure/external/aegis-checkpoint.json
# Copy the checkpoint and protect the key outside the audit host.
aegis audit verify --checkpoint /secure/external/aegis-checkpoint.json
```

The environment variable is a UTF-8 secret and must contain at least 32 bytes. Do not commit it, place it in shell history, or store the checkpoint beside the database if both are exposed to the same attacker. The CLI writes checkpoint files with owner-only permissions where supported.

## Scheduled verification

The security workflow runs daily and on manual dispatch. It installs the project and development dependencies, audits the installed Python environment with pip-audit, and repeats lint, formatting, compilation, and tests. CodeQL analyzes Python changes on pushes, pull requests, and weekly schedule. Dependabot proposes dependency and GitHub Actions updates weekly; updates are not auto-merged.


## Input and provider-output budgets

The runtime rejects prompts above 32,768 characters and system prompts above 16,384 characters by default. The provider adapter caps assistant content at 65,536 characters, tool calls at 128 per response, and each raw tool-argument JSON payload at 65,536 characters. Runtime validation additionally rejects duplicate or malformed tool-call identifiers, non-finite JSON numbers, non-string object keys, and aggregate tool arguments above 262,144 characters per turn. These are resource-exhaustion guardrails, not a sandbox for tool handler code.


Synchronous tool handlers and synchronous approval callbacks are dispatched to worker threads. This protects the event loop from ordinary blocking I/O, but does not forcibly terminate a stuck thread and does not isolate handler permissions. Use a separate operating-system sandbox or container for untrusted tools.
