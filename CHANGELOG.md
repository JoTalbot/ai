# Changelog

All notable changes to Aegis AI Runtime are documented here.

## 0.1.0 - 2026-11-11

### Added
- Provider-neutral asynchronous agent runtime.
- OpenAI-compatible Chat Completions adapter using the Python standard library.
- Tool registry with JSON-schema-subset validation.
- Default policy allowing read-only tools, requiring explicit approval for writes and external actions, and denying destructive actions.
- SQLite audit journal with a verifiable SHA-256 hash chain and metadata-only event payloads.
- Bounded calculator tool, CLI, demo, documentation, and CI.
