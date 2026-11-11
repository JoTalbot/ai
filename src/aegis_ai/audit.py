"""SQLite audit journal with a verifiable SHA-256 hash chain."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


_GENESIS = "0" * 64
_SENSITIVE_MARKERS = ("password", "secret", "token", "api_key", "authorization", "credential")


@dataclass(frozen=True, slots=True)
class AuditVerification:
    ok: bool
    event_count: int
    error: str | None = None


class AuditLog:
    def __init__(self, path: str | Path) -> None:
        raw_path = str(path)
        self._memory = raw_path == ":memory:"
        self.path = raw_path if self._memory else str(Path(raw_path).expanduser())
        self._lock = threading.Lock()
        self._memory_connection: sqlite3.Connection | None = None
        if self._memory:
            self._memory_connection = sqlite3.connect(
                ":memory:",
                timeout=10,
                check_same_thread=False,
            )
            self._memory_connection.row_factory = sqlite3.Row
        else:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        if self._memory_connection is not None:
            connection = self._memory_connection
        else:
            connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _close(self, connection: sqlite3.Connection) -> None:
        if not self._memory:
            connection.close()

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL
                )
                """
            )
            connection.commit()
        finally:
            self._close(connection)
        if not self._memory:
            try:
                Path(self.path).chmod(0o600)
            except OSError:
                pass

    def append(self, run_id: str, event: str, payload: Mapping[str, Any] | None = None) -> str:
        safe_payload = _redact(dict(payload or {}))
        payload_json = _canonical(safe_payload)
        timestamp = datetime.now(UTC).isoformat(timespec="milliseconds")

        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT event_hash FROM audit_events ORDER BY id DESC LIMIT 1"
                ).fetchone()
                previous_hash = row["event_hash"] if row else _GENESIS
                record = {
                    "run_id": run_id,
                    "event": event,
                    "timestamp": timestamp,
                    "payload": safe_payload,
                    "previous_hash": previous_hash,
                }
                event_hash = hashlib.sha256(_canonical(record).encode("utf-8")).hexdigest()
                connection.execute(
                    """
                    INSERT INTO audit_events
                        (run_id, event, timestamp, payload_json, previous_hash, event_hash)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (run_id, event, timestamp, payload_json, previous_hash, event_hash),
                )
                connection.commit()
                return event_hash
            except Exception:
                connection.rollback()
                raise
            finally:
                self._close(connection)

    def verify(self) -> AuditVerification:
        previous_hash = _GENESIS
        count = 0
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT id, run_id, event, timestamp, payload_json, previous_hash, event_hash
                FROM audit_events ORDER BY id ASC
                """
            ).fetchall()
        finally:
            self._close(connection)

        for row in rows:
            count += 1
            if row["previous_hash"] != previous_hash:
                return AuditVerification(
                    False, count, f"broken previous-hash link at event {row['id']}"
                )
            try:
                payload = json.loads(row["payload_json"])
            except json.JSONDecodeError:
                return AuditVerification(False, count, f"invalid payload JSON at event {row['id']}")
            record = {
                "run_id": row["run_id"],
                "event": row["event"],
                "timestamp": row["timestamp"],
                "payload": payload,
                "previous_hash": row["previous_hash"],
            }
            expected = hashlib.sha256(_canonical(record).encode("utf-8")).hexdigest()
            if not hmac.compare_digest(expected, row["event_hash"]):
                return AuditVerification(False, count, f"event hash mismatch at event {row['id']}")
            previous_hash = row["event_hash"]

        return AuditVerification(True, count)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if any(marker in str(key).casefold() for marker in _SENSITIVE_MARKERS)
                else _redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
