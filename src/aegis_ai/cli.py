"""Command-line interface for local Aegis development and operation."""

from __future__ import annotations

import argparse
import asyncio
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

    audit = commands.add_parser("audit", help="inspect the local audit journal")
    audit_commands = audit.add_subparsers(dest="audit_command", required=True)
    verify = audit_commands.add_parser("verify", help="verify the audit hash chain")
    verify.add_argument("--db", type=Path)
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
            return _verify_audit(args.db or settings.audit_db)
        if args.command == "run":
            if args.max_steps < 1:
                raise ConfigurationError("--max-steps must be at least 1")
            settings = replace(
                settings,
                base_url=args.base_url.rstrip("/") if args.base_url else settings.base_url,
                model=args.model or settings.model,
                audit_db=args.audit_db or settings.audit_db,
            )
            return asyncio.run(_run_prompt(settings, args.prompt, args.max_steps))
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


def _verify_audit(path: Path) -> int:
    result = AuditLog(path).verify()
    if result.ok:
        print(f"Audit chain valid: {result.event_count} event(s)")
        return 0
    print(
        f"Audit chain INVALID after {result.event_count} event(s): {result.error}",
        file=sys.stderr,
    )
    return 1


async def _run_prompt(settings: Settings, prompt: str, max_steps: int) -> int:
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
    )
    result = await runtime.run(prompt)
    if result.output:
        print(result.output)
    print(
        f"Run {result.run_id}: {result.status.value}; "
        f"{result.steps} step(s), {result.tool_calls} tool call(s)"
    )
    return 0 if result.status.value == "completed" else 1
