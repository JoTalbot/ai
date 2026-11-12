"""SQLite audit journal with a verifiable SHA-256 hash chain and signed checkpoints."""

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
_SENSITIVE_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "authorization",
    "credential",
    "privatekey",
    "accesskey",
    "refreshkey",
    "signingkey",
)
_CHECKPOINT_VERSION = 1
_MIN_SIGNING_KEY_BYTES = 32


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
                ":memory:", timeout=10, check_same_thread=False
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

    def _snapshot(self) -> tuple[AuditVerification, int | None, str]:
        # Read the chain and its tail in one SQLite snapshot. Without this transaction,
        # a concurrent append could make a freshly created checkpoint internally inconsistent.
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN")
                rows = connection.execute(
                    """
                    SELECT id, run_id, event, timestamp, payload_json, previous_hash, event_hash
                    FROM audit_events ORDER BY id ASC
                    """
                ).fetchall()
                verification = _verify_rows(rows)
                last_id = rows[-1]["id"] if rows else None
                last_hash = rows[-1]["event_hash"] if rows else _GENESIS
                connection.commit()
                return verification, last_id, last_hash
            except Exception:
                connection.rollback()
                raise
            finally:
                self._close(connection)

    def verify(self) -> AuditVerification:
        return self._snapshot()[0]

    def create_checkpoint(self, signing_key: bytes) -> dict[str, Any]:
        """Create an HMAC-authenticated snapshot for storage outside the audit host."""
        _validate_signing_key(signing_key)
        verification, last_id, last_hash = self._snapshot()
        if not verification.ok:
            raise ValueError(f"cannot checkpoint an invalid audit chain: {verification.error}")

        payload = {
            "version": _CHECKPOINT_VERSION,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "event_count": verification.event_count,
            "last_event_id": last_id,
            "last_event_hash": last_hash,
        }
        signature = hmac.new(signing_key, _canonical(payload).encode("utf-8"), hashlib.sha256)
        return {**payload, "signature": signature.hexdigest()}

    def verify_checkpoint(
        self, checkpoint: Mapping[str, Any], signing_key: bytes
    ) -> AuditVerification:
        """Verify both the local chain and its externally retained signed tail checkpoint."""
        _validate_signing_key(signing_key)
        if not isinstance(checkpoint, Mapping):
            return AuditVerification(False, 0, "checkpoint must be an object")

        payload = {key: value for key, value in checkpoint.items() if key != "signature"}
        signature = checkpoint.get("signature")
        if not isinstance(signature, str):
            return AuditVerification(False, 0, "checkpoint signature is missing")
        expected = hmac.new(
            signing_key, _canonical(payload).encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return AuditVerification(False, 0, "checkpoint signature mismatch")
        if payload.get("version") != _CHECKPOINT_VERSION:
            return AuditVerification(False, 0, "unsupported checkpoint version")

        verification, current_id, current_hash = self._snapshot()
        if not verification.ok:
            return verification
        if (
            payload.get("event_count") != verification.event_count
            or payload.get("last_event_id") != current_id
            or payload.get("last_event_hash") != current_hash
        ):
            return AuditVerification(
                False,
                verification.event_count,
                "audit database does not match the signed checkpoint",
            )
        return verification


def _verify_rows(rows: list[sqlite3.Row]) -> AuditVerification:
    previous_hash = _GENESIS
    count = 0
    for row in rows:
        count += 1
        if row["previous_hash"] != previous_hash:
            return AuditVerification(
                False, count, f"broken previous-hash link at event {row['id']}"
            )
        try:
            payload = json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError):
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


def _validate_signing_key(signing_key: bytes) -> None:
    if not isinstance(signing_key, bytes) or len(signing_key) < _MIN_SIGNING_KEY_BYTES:
        raise ValueError("checkpoint signing key must contain at least 32 bytes")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _normalize_sensitive_key(key: Any) -> str:
    """Normalize key spelling so snake_case, camelCase, and hyphenated secrets match."""
    return "".join(character for character in str(key).casefold() if character.isalnum())


def _is_sensitive_key(key: Any) -> bool:
    normalized = _normalize_sensitive_key(key)
    for marker in _SENSITIVE_MARKERS:
        if marker == "token":
            if normalized == marker or normalized.endswith(marker):
                return True
        elif marker in normalized:
            return True
    return False


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if _is_sensitive_key(key)
                else _redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
