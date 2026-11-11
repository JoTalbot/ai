import json
import sqlite3

import pytest

from aegis_ai.audit import AuditLog


def test_audit_chain_verifies_and_redacts_secrets(tmp_path):
    database = tmp_path / "audit.db"
    audit = AuditLog(database)
    audit.append("run-1", "started", {"model": "local", "api_key": "do-not-store", "step": 1})
    audit.append("run-1", "completed", {"tool": "calculator"})

    result = audit.verify()

    assert result.ok
    assert result.event_count == 2
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT payload_json FROM audit_events LIMIT 1").fetchone()
    assert "do-not-store" not in row[0]
    assert "[REDACTED]" in row[0]


def test_audit_chain_detects_payload_tampering(tmp_path):
    database = tmp_path / "audit.db"
    audit = AuditLog(database)
    audit.append("run-1", "started", {"step": 1})

    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE audit_events SET payload_json = ?", ('{"step":2}',))

    result = audit.verify()

    assert not result.ok
    assert "hash mismatch" in result.error


def test_in_memory_audit_keeps_the_same_connection():
    audit = AuditLog(":memory:")
    audit.append("run-memory", "started", {"step": 1})

    result = audit.verify()

    assert result.ok
    assert result.event_count == 1


def test_signed_checkpoint_verifies_and_detects_tail_truncation(tmp_path):
    database = tmp_path / "audit.db"
    audit = AuditLog(database)
    key = b"test-checkpoint-key-that-is-at-least-32-bytes"
    audit.append("run-1", "started", {"step": 1})
    audit.append("run-1", "completed", {"step": 2})
    checkpoint = audit.create_checkpoint(key)

    assert audit.verify_checkpoint(checkpoint, key).ok

    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM audit_events WHERE id = 2")
    result = audit.verify_checkpoint(checkpoint, key)

    assert not result.ok
    assert "checkpoint" in result.error


def test_signed_checkpoint_rejects_tampering_and_wrong_key(tmp_path):
    audit = AuditLog(tmp_path / "audit.db")
    key = b"test-checkpoint-key-that-is-at-least-32-bytes"
    checkpoint = audit.create_checkpoint(key)
    altered = {**checkpoint, "event_count": 99}

    assert not audit.verify_checkpoint(altered, key).ok
    assert not audit.verify_checkpoint(checkpoint, b"another-key-that-is-also-32-bytes").ok


def test_checkpoint_requires_a_strong_key(tmp_path):
    with pytest.raises(ValueError, match="at least 32 bytes"):
        AuditLog(tmp_path / "audit.db").create_checkpoint(b"too-short")


def test_checkpoint_refuses_invalid_audit_chain(tmp_path):
    database = tmp_path / "audit.db"
    audit = AuditLog(database)
    audit.append("run-1", "started", {"step": 1})
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE audit_events SET event_hash = ?", ("f" * 64,))

    with pytest.raises(ValueError, match="invalid audit chain"):
        audit.create_checkpoint(b"test-checkpoint-key-that-is-at-least-32-bytes")
