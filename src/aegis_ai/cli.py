"""Command-line interface for local Aegis development and operation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from aegis_ai.audit import AuditLog
from aegis_ai.builtins import calculator_tool
from aegis_ai.config import ConfigurationError, Settings
from aegis_ai.demo import run_demo
from aegis_ai.policy import SafePolicy
from aegis_ai.provider import OpenAICompatibleProvider
from aegis_ai.registry import ToolRegistry
from aegis_ai.runtime import AgentRuntime


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aegis", description="Safe-by-default AI agent runtime")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("demo", help="run an offline deterministic demonstration")
    commands.add_parser("doctor", help="validate local configuration without making a network call")

    run = commands.add_parser("run", help="run a prompt with the configured model")
    run.add_argument("prompt")
    run.add_argument("--base-url")
    run.add_argument("--model")
    run.add_argument("--audit-db", type=Path)
    run.add_argument("--max-steps", type=int, default=8)
    run.add_argument("--max-tool-calls", type=int, default=32)

    audit = commands.add_parser("audit", help="inspect the local audit journal")
    audit_commands = audit.add_subparsers(dest="audit_command", required=True)
    verify = audit_commands.add_parser("verify", help="verify the audit hash chain")
    verify.add_argument("--db", type=Path)
    verify.add_argument("--checkpoint", type=Path)
    verify.add_argument("--key-env", default="AEGIS_AUDIT_CHECKPOINT_KEY")
    checkpoint = audit_commands.add_parser("checkpoint", help="create a signed audit checkpoint")
    checkpoint.add_argument("--db", type=Path)
    checkpoint.add_argument("--output", type=Path, required=True)
    checkpoint.add_argument("--key-env", default="AEGIS_AUDIT_CHECKPOINT_KEY")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "demo":
            return run_demo()
        settings = Settings.from_env()
        if args.command == "doctor":
            return _doctor(settings)
        if args.command == "audit":
            database = args.db or settings.audit_db
            if args.audit_command == "checkpoint":
                return _create_audit_checkpoint(database, args.output, args.key_env)
            return _verify_audit(database, args.checkpoint, args.key_env)
        if args.command == "run":
            if args.max_steps < 1:
                raise ConfigurationError("--max-steps must be at least 1")
            if args.max_tool_calls < 1:
                raise ConfigurationError("--max-tool-calls must be at least 1")
            settings = replace(
                settings,
                base_url=args.base_url.rstrip("/") if args.base_url else settings.base_url,
                model=args.model or settings.model,
                audit_db=args.audit_db or settings.audit_db,
            )
            return asyncio.run(
                _run_prompt(settings, args.prompt, args.max_steps, args.max_tool_calls)
            )
    except (ConfigurationError, OSError, RuntimeError, ValueError) as exc:
        print(f"aegis: {exc}", file=sys.stderr)
        return 2
    return 2


def _doctor(settings: Settings) -> int:
    print("Aegis configuration: valid")
    print(f"Provider: {settings.base_url}")
    print(f"Model: {settings.model}")
    key_status = "set" if settings.api_key else "not set"
    print(f"API key: {key_status}")
    print(f"Audit database: {settings.audit_db}")
    print("Network check: skipped")
    return 0


def _checkpoint_key(environment_name: str) -> bytes:
    value = os.environ.get(environment_name)
    if not value:
        raise ConfigurationError(
            f"checkpoint key is missing; set the {environment_name} environment variable"
        )
    key = value.encode("utf-8")
    if len(key) < 32:
        raise ConfigurationError(f"{environment_name} must contain at least 32 UTF-8 bytes")
    return key


def _create_audit_checkpoint(database: Path, output: Path, key_env: str) -> int:
    checkpoint = AuditLog(database).create_checkpoint(_checkpoint_key(key_env))
    output = output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    try:
        temporary.write_text(json.dumps(checkpoint, indent=2) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()
    print(f"Signed audit checkpoint written: {output}")
    print(f"Events covered: {checkpoint['event_count']}")
    print("Store this file and its signing key outside the audit host.")
    return 0


def _verify_audit(database: Path, checkpoint_path: Path | None, key_env: str) -> int:
    audit = AuditLog(database)
    if checkpoint_path is None:
        result = audit.verify()
    else:
        checkpoint = json.loads(checkpoint_path.expanduser().read_text(encoding="utf-8"))
        result = audit.verify_checkpoint(checkpoint, _checkpoint_key(key_env))
    if result.ok:
        label = (
            "Audit chain and signed checkpoint valid"
            if checkpoint_path
            else "Audit chain valid"
        )
        print(f"{label}: {result.event_count} event(s)")
        return 0
    print(
        f"Audit verification INVALID after {result.event_count} event(s): {result.error}",
        file=sys.stderr,
    )
    return 1


async def _run_prompt(
    settings: Settings,
    prompt: str,
    max_steps: int,
    max_tool_calls: int,
) -> int:
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
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
    )
    result = await runtime.run(prompt)
    if result.output:
        print(result.output)
    print(
        f"Run {result.run_id}: {result.status.value}; "
        f"{result.steps} step(s), {result.tool_calls} tool call(s)"
    )
    return 0 if result.status.value == "completed" else 1
