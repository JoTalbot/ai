# Architecture

Aegis is split into five small boundaries. The model is deliberately not the authority for any of them.

## Request path

1. The host submits a user prompt to AgentRuntime.
2. AgentRuntime sends the conversation and registered tool schemas to a ModelProvider.
3. The provider returns assistant text and/or structured tool calls.
4. ToolRegistry resolves the name and validates the arguments against the registered schema.
5. SafePolicy classifies the tool by its declared risk. A write or external action is blocked unless the host supplies an approval callback and that callback approves. Destructive actions are denied by default.
6. Only after validation and policy approval does the runtime invoke the handler.
7. The runtime appends tool results to the in-memory conversation and asks the provider for the next turn.
8. AuditLog stores minimal metadata about each transition.

## Modules

- models.py: immutable messages, tool contracts, provider protocol types, and run results.
- provider.py: OpenAI-compatible HTTP adapter. No vendor SDK is required.
- registry.py: tool registration, schema export, argument validation, and invocation.
- policy.py: deterministic risk decisions.
- runtime.py: bounded agent loop and approval boundary.
- audit.py: SQLite event journal and hash-chain verification.
- builtins.py: a safe, bounded calculator demonstration.
- cli.py: local developer interface.

## Trust boundaries

The model output is untrusted input. Tool names, argument JSON, and claims of user consent are never accepted as authority. Registered handlers are trusted code and run with host process permissions. The host application is responsible for authentic approval and operating-system isolation.

## Failure behavior

- Invalid tool arguments never reach a handler.
- Unknown tools are returned to the model as failed tool results.
- Missing approval fails closed.
- Provider failures are recorded by error type and re-raised.
- The loop stops after max_steps rather than running indefinitely.
- Audit write failures propagate; the runtime does not silently claim an event was recorded.

## Audit integrity

Each row hashes a canonical JSON representation of its event fields plus the previous row hash. Verification checks both the previous-hash link and the event hash. This detects edits, deletions, reordering, and broken links among retained rows. It cannot detect removal of the final tail without an independent checkpoint, or prove that an attacker with full control of the host did not replace the entire database. Signed checkpoints stored separately are required for those threat models.
