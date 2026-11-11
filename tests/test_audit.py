import sqlite3

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
