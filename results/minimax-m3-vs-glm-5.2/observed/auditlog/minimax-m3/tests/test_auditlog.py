"""Tests for auditlog. Run with: python -m pytest -q"""

import json
import os
import sqlite3
import tempfile

import pytest

from auditlog import AuditLog, Entry, redact, verify_chain
from auditlog.core import REDACTED


@pytest.fixture
def tmp_path():
    with tempfile.TemporaryDirectory() as d:
        yield os.path.join(d, "audit.db")


# ---------------------------------------------------------------------------
# Basic recording
# ---------------------------------------------------------------------------


def test_record_minimal(tmp_path):
    log = AuditLog(tmp_path)
    e = log.record(actor="alice", action="login")
    assert e.actor == "alice"
    assert e.action == "login"
    assert e.target is None
    assert e.details == {}
    assert e.redacted is False
    assert e.sequence == 1
    assert e.prev_hash == "0" * 64
    assert len(e.hash) == 64


def test_record_full(tmp_path):
    log = AuditLog(tmp_path)
    e = log.record(
        actor="alice",
        action="update",
        target="user:42",
        details={"role": "admin", "fields": ["email"]},
        timestamp=1_700_000_000.0,
    )
    assert e.target == "user:42"
    assert e.details == {"role": "admin", "fields": ["email"]}
    assert e.timestamp == 1_700_000_000.0


def test_record_requires_actor_and_action(tmp_path):
    log = AuditLog(tmp_path)
    with pytest.raises(ValueError):
        log.record(actor="", action="x")
    with pytest.raises(ValueError):
        log.record(actor="x", action="")


def test_sequence_monotonic(tmp_path):
    log = AuditLog(tmp_path)
    for i in range(5):
        e = log.record(actor="alice", action="ping", details={"i": i})
        assert e.sequence == i + 1


def test_chain_links(tmp_path):
    log = AuditLog(tmp_path)
    e1 = log.record(actor="a", action="x")
    e2 = log.record(actor="a", action="x")
    e3 = log.record(actor="a", action="x")
    assert e2.prev_hash == e1.hash
    assert e3.prev_hash == e2.hash


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_redact_replaces_value(tmp_path):
    log = AuditLog(tmp_path)
    e = log.record(
        actor="alice",
        action="update",
        target="user:42",
        details={"password": redact("hunter2"), "role": "admin"},
    )
    assert e.details == {"password": REDACTED, "role": "admin"}
    assert e.redacted is True


def test_redact_nested(tmp_path):
    log = AuditLog(tmp_path)
    e = log.record(
        actor="alice",
        action="update",
        details={
            "user": {"name": "alice", "ssn": redact("123-45-6789")},
            "tokens": [redact("abc"), "kept"],
        },
    )
    assert e.details["user"]["ssn"] == REDACTED
    assert e.details["user"]["name"] == "alice"
    assert e.details["tokens"] == [REDACTED, "kept"]
    assert e.redacted is True


def test_no_redaction_flag_when_unused(tmp_path):
    log = AuditLog(tmp_path)
    e = log.record(actor="a", action="x", details={"ok": 1})
    assert e.redacted is False


def test_redacted_value_not_persisted(tmp_path):
    """The raw sensitive value must not appear anywhere on disk."""
    log = AuditLog(tmp_path)
    secret = "super-secret-token-xyz"
    log.record(actor="a", action="x", details={"token": redact(secret)})
    log.close()
    with open(tmp_path, "rb") as f:
        raw = f.read()
    assert secret.encode() not in raw


# ---------------------------------------------------------------------------
# Querying
# ---------------------------------------------------------------------------


def test_query_by_actor(tmp_path):
    log = AuditLog(tmp_path)
    log.record(actor="alice", action="login")
    log.record(actor="bob", action="login")
    log.record(actor="alice", action="logout")
    rows = log.query(actor="alice")
    assert len(rows) == 2
    assert all(r.actor == "alice" for r in rows)
    # Newest first
    assert rows[0].action == "logout"
    assert rows[1].action == "login"


def test_query_by_action_and_target(tmp_path):
    log = AuditLog(tmp_path)
    log.record(actor="a", action="update", target="user:1")
    log.record(actor="a", action="delete", target="user:1")
    log.record(actor="a", action="update", target="user:2")
    assert len(log.query(action="update")) == 2
    assert len(log.query(action="update", target="user:1")) == 1
    assert len(log.query(target="user:1")) == 2


def test_query_time_window(tmp_path):
    log = AuditLog(tmp_path)
    log.record(actor="a", action="x", timestamp=100.0)
    log.record(actor="a", action="x", timestamp=200.0)
    log.record(actor="a", action="x", timestamp=300.0)
    assert len(log.query(since=150.0, until=250.0)) == 1
    assert len(log.query(since=200.0)) == 2
    assert len(log.query(until=200.0)) == 2


def test_query_limit(tmp_path):
    log = AuditLog(tmp_path)
    for i in range(10):
        log.record(actor="a", action="x", details={"i": i})
    assert len(log.query(limit=3)) == 3


def test_get_by_id(tmp_path):
    log = AuditLog(tmp_path)
    e = log.record(actor="a", action="x")
    fetched = log.get(e.id)
    assert fetched is not None
    assert fetched.id == e.id
    assert log.get("nonexistent") is None


def test_iter_chronological(tmp_path):
    log = AuditLog(tmp_path)
    log.record(actor="a", action="x", timestamp=1.0)
    log.record(actor="a", action="y", timestamp=2.0)
    log.record(actor="a", action="z", timestamp=3.0)
    actions = [e.action for e in log]
    assert actions == ["x", "y", "z"]


def test_len(tmp_path):
    log = AuditLog(tmp_path)
    assert len(log) == 0
    log.record(actor="a", action="x")
    log.record(actor="a", action="y")
    assert len(log) == 2


# ---------------------------------------------------------------------------
# Context manager / decorator
# ---------------------------------------------------------------------------


def test_audit_context_success(tmp_path):
    log = AuditLog(tmp_path)
    with log.audit(actor="alice", action="delete", target="user:42") as ctx:
        ctx["rows"] = 3
    rows = log.query()
    assert len(rows) == 1
    assert rows[0].details == {"rows": 3, "ok": True}


def test_audit_context_exception(tmp_path):
    log = AuditLog(tmp_path)
    with pytest.raises(RuntimeError):
        with log.audit(actor="alice", action="delete", target="user:42") as ctx:
            ctx["rows"] = 0
            raise RuntimeError("boom")
    rows = log.query()
    assert len(rows) == 1
    assert rows[0].details["error"] == "RuntimeError"
    assert rows[0].details["ok"] is False


def test_audit_decorator(tmp_path):
    log = AuditLog(tmp_path)

    @log.audit_call(actor="alice", action="compute")
    def add(a, b):
        return a + b

    assert add(2, 3) == 5
    rows = log.query()
    assert len(rows) == 1
    assert rows[0].details["result"].startswith("5")


# ---------------------------------------------------------------------------
# Integrity / hash chain
# ---------------------------------------------------------------------------


def test_verify_chain_clean(tmp_path):
    log = AuditLog(tmp_path)
    for i in range(5):
        log.record(actor="a", action="x", details={"i": i})
    log.close()
    ok, msg = verify_chain(tmp_path)
    assert ok is True
    assert msg is None


def test_verify_chain_detects_tampering(tmp_path):
    log = AuditLog(tmp_path)
    log.record(actor="alice", action="login")
    log.record(actor="alice", action="update", target="user:42",
               details={"role": "admin"})
    log.record(actor="alice", action="logout")
    log.close()

    # Tamper: change the actor on the middle entry directly in SQLite.
    conn = sqlite3.connect(tmp_path)
    conn.execute(
        "UPDATE entries SET actor = ? WHERE sequence = 2",
        ("mallory",),
    )
    conn.commit()
    conn.close()

    ok, msg = verify_chain(tmp_path)
    assert ok is False
    assert "sequence 2" in msg


def test_verify_chain_detects_deleted_entry(tmp_path):
    log = AuditLog(tmp_path)
    log.record(actor="a", action="x")
    log.record(actor="a", action="y")
    log.record(actor="a", action="z")
    log.close()

    conn = sqlite3.connect(tmp_path)
    conn.execute("DELETE FROM entries WHERE sequence = 2")
    conn.commit()
    conn.close()

    ok, msg = verify_chain(tmp_path)
    assert ok is False
    # Either a sequence gap or a prev_hash mismatch — both are valid
    # detections of the deletion.
    assert msg is not None


def test_signed_chain_verifies(tmp_path):
    secret = b"shared-secret-keep-it-safe"
    log = AuditLog(tmp_path, secret=secret)
    log.record(actor="a", action="x")
    log.record(actor="a", action="y", details={"k": redact("v")})
    log.close()
    ok, msg = verify_chain(tmp_path, secret=secret)
    assert ok is True
    assert msg is None


def test_signed_chain_detects_wrong_secret(tmp_path):
    log = AuditLog(tmp_path, secret=b"right-secret")
    log.record(actor="a", action="x")
    log.close()
    ok, msg = verify_chain(tmp_path, secret=b"wrong-secret")
    assert ok is False
    assert "signature" in msg


def test_signed_chain_detects_tampering(tmp_path):
    secret = b"k"
    log = AuditLog(tmp_path, secret=secret)
    log.record(actor="alice", action="login")
    log.record(actor="alice", action="logout")
    log.close()

    conn = sqlite3.connect(tmp_path)
    conn.execute("UPDATE entries SET actor = ? WHERE sequence = 1", ("mallory",))
    conn.commit()
    conn.close()

    ok, msg = verify_chain(tmp_path, secret=secret)
    assert ok is False


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def test_in_memory_log():
    log = AuditLog(":memory:")
    log.record(actor="a", action="x")
    log.record(actor="b", action="y")
    assert len(log) == 2


def test_entry_is_frozen():
    e = Entry(
        id="x", timestamp=0.0, actor="a", action="x", target=None,
        details={}, redacted=False, prev_hash="0" * 64, hash="0" * 64,
        sequence=1,
    )
    with pytest.raises(Exception):
        e.actor = "b"  # type: ignore[misc]


def test_details_round_trip_through_json(tmp_path):
    """Non-ASCII keys/values survive a write/read cycle."""
    log = AuditLog(tmp_path)
    log.record(
        actor="alice",
        action="update",
        details={"name": "Élodie", "tags": ["café", "naïve"]},
    )
    [e] = log.query()
    assert e.details == {"name": "Élodie", "tags": ["café", "naïve"]}


def test_context_manager_protocol(tmp_path):
    with AuditLog(tmp_path) as log:
        log.record(actor="a", action="x")
    # Should be closed; reopening should still see the entry.
    log2 = AuditLog(tmp_path)
    assert len(log2) == 1
