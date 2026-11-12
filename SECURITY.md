# Security policy

## Scope

Aegis is an early-stage agent runtime. It provides guardrails, not a complete security boundary for arbitrary Python code. A tool handler executes with the operating-system permissions of the host process.

## Report a vulnerability

Please do not publish exploitable details in a public issue. Use GitHub's private vulnerability reporting for this repository when available. Include affected versions, a minimal reproduction, impact, and any suggested mitigation. Do not attach credentials or real user data.

## Runtime guarantees

- Tool arguments are validated before the handler is called.
- Read-only tools are the only tools allowed by the default policy.
- Write and external tools require an explicit host approval callback.
- Destructive tools are denied by the default policy.
- The model cannot grant itself approval by emitting text.
- The default CLI registers only a bounded calculator tool.
- Audit events omit prompts, completions, tool outputs, and raw argument values. Sensitive metadata keys are normalized before redaction to catch common snake_case, camelCase, and hyphenated variants.
- Prompt size, provider content, per-call arguments, aggregate per-turn arguments, tool results, and total tool calls are bounded.
- Signed HMAC checkpoints can detect audit database rollback or tail truncation when retained independently.

## Important limitations

- Aegis does not sandbox Python tool handlers. Run untrusted or third-party tools in a separate OS/container boundary with least privilege.
- Approval callbacks are trusted host code. They must obtain consent through a trusted channel, not from model-generated content.
- A local audit hash chain is tamper-evident, not tamper-proof. An HMAC checkpoint detects rollback only while the checkpoint and its secret key remain outside the attacker's control. HMAC is not a public-key signature.
- An OpenAI-compatible endpoint receives the prompt and tool schemas. Review the provider's data handling before sending sensitive content.
- Provider URL validation does not replace outbound network/egress policy.
- Do not expose a local model gateway or Aegis process to an untrusted network without authentication, transport security, and access controls.

## Safe deployment checklist

- Use a dedicated, unprivileged OS account or the hardened container image described in `docs/deployment.md`.
- Keep API keys and checkpoint signing keys in a secret manager, never in source control.
- Restrict outbound network access to approved providers.
- Register only the tools required by the workflow.
- Mark tools according to their real side effects; never label a write as read-only.
- Export signed audit checkpoints to storage controlled separately from the runtime host.
- Require human review for high-impact and irreversible actions.
- Review CodeQL alerts and Dependabot pull requests before merging.
