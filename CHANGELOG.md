# Changelog

All notable changes to Aegis AI Runtime are documented here.

## 0.2.0 - 2026-11-11

### Security and runtime hardening

- Validate provider endpoint URLs, credentials, query strings, fragments, model names, and finite positive timeouts.
- Reject duplicate tool-call IDs and empty tool names from provider responses.
- Ignore negative and boolean token counters in provider usage metadata.
- Bound tool-result payloads before they are returned to the model.
- Expose the global tool-call budget through the CLI.
- Add provider validation, oversized-response, duplicate-ID, and bounded-tool-output tests.
- Add a scheduled dependency audit and full verification workflow.

### Operational notes

- The runtime still executes registered Python handlers inside the host process.
- The scheduled security workflow audits installed Python dependencies; it is not a substitute for OS-level isolation or a complete software supply-chain review.
