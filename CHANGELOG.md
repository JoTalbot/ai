# Changelog

## 0.4.1 - 2026-11-12

- Normalize sensitive audit payload keys before redaction, covering camelCase, hyphenated, and compact spellings such as `apiKey`, `client-secret`, and `privateKey`.
- Add regression coverage for nested secret fields while preserving non-sensitive metadata.

## 0.4.0 - 2026-11-11

- Bound prompt and system-prompt sizes before provider calls.
- Reject malformed, duplicate, oversized, and non-finite tool-call arguments before runtime dispatch.
- Bound provider response content, tool-call count, and per-call argument payloads.
- Add per-turn and per-argument budgets to prevent oversized provider output from exhausting runtime memory.

## 0.3.0 - 2026-11-11

- Add HMAC-SHA256 signed audit checkpoints that can be stored outside the runtime host.
- Detect audit database tail truncation when verifying against an independently retained checkpoint.
- Add CLI commands to create and verify signed audit checkpoints.
- Add CodeQL static analysis and weekly Dependabot update proposals.

## 0.2.0 - 2026-11-11

- Harden provider endpoint and response validation.
- Bound tool-call counts and tool-result sizes.
- Add scheduled dependency auditing and architecture/security documentation.

## 0.1.0

- Initial provider-agnostic agent runtime, safe tool policy, and SQLite audit journal.
