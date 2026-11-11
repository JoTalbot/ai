# Contributing

Thanks for helping improve Aegis. The project values small interfaces, explicit security boundaries, and reproducible behavior.

## Before opening a change

1. Read README.md and SECURITY.md.
2. Open an issue for a substantial design change so its security and compatibility impact can be discussed.
3. Keep the core dependency-free. New runtime dependencies need a clear, documented reason.
4. Add tests for behavior and failure paths. A test that only checks the happy path is not enough for policy or execution code.

## Local checks

```bash
python -m pip install -e ".[dev]"
ruff check .
ruff format --check .
pytest
```

## Pull requests

- Explain the problem, the chosen approach, and alternatives considered.
- Describe security implications and any new permissions.
- Include a migration note for behavior or public API changes.
- Never include credentials, production data, or private audit databases.
- Do not weaken a default policy merely to make a test or demo pass.

## Design rules

- Fail closed when validation or approval is missing.
- Keep provider-specific code behind the provider protocol.
- Keep tool handlers explicit and independently testable.
- Audit metadata, not conversation content or secret-bearing argument values.
- Avoid network, filesystem, shell, and irreversible actions in built-in tools.
