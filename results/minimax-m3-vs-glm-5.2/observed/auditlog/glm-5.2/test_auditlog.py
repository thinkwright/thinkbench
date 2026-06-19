import json
import os
import sqlite3
import tempfile

import pytest

import auditlog
from auditlog import AuditLog, TamperError, redact


@pytest.fixture
def log_path(tmp_path):
    return str(tmp_path / "audit.db")


@pytest.fixture
def log(log_path):
    al = AuditLog(log_path)
    yield al
    al.close()


# -- recording ------------------------------------------------------------


def test_log_returns_entry_with_expected_fields(log):
    e = log.log(actor="alice", action="update", target="setting:timeout",
                details={"old": 30, "new": 60})
    assert e.actor == "alice"
    assert e.action == "update"
    assert e.target == "setting:timeout"
    assert e.details == {"old": 30, "new": 60}
    assert e.seq == 1
    assert e.id == "0000000001"
    assert e.prev_hash == "GENESIS"
    assert e.hash and e.hash != "GENESIS"
    assert "T" in e.when  # ISO timestamp


def test_log_requires_actor_and_action(log):
    with pytest.raises(ValueError):
        log.log(actor="", action="x")
    with pytest.raises(ValueError):
        log.log(actor="x", action="")


def test_log_defaults_target_and_details(log):
    e = log.log(actor="bob", action="login")
    assert e.target is None
    assert e.details == {}


def test_entries_get_increasing_seq(log):
    a = log.log(actor="a", action="x")
    b = log.log(actor="a", action="y")
    assert b.seq == a.seq + 1
    assert b.prev_hash == a.hash


# -- querying -------------------------------------------------------------


def test_query_all_returns_in_order(log):
    log.log(actor="alice", action="a")
    log.log(actor="bob", action="b")
    log.log(actor="alice", action="c")
    rows = log.query().all()
    assert [e.action for e in rows] == ["a", "b", "c"]


def test_query_by_actor(log):
    log.log(actor="alice", action="a")
    log.log(actor="bob", action="b")
    log.log(actor="alice", action="c")
    rows = log.query().actor("alice").all()
    assert [e.action for e in rows] == ["a", "c"]


def test_query_by_target(log):
    log.log(actor="alice", action="update", target="setting:timeout")
    log.log(actor="bob", action="update", target="setting:theme")
    rows = log.query().target("setting:timeout").all()
    assert len(rows) == 1
    assert rows[0].actor == "alice"


def test_query_by_action(log):
    log.log(actor="a", action="login")
    log.log(actor="b", action="logout")
    rows = log.query().action("login").all()
    assert len(rows) == 1


def test_query_chained_filters(log):
    log.log(actor="alice", action="update", target="t1")
    log.log(actor="alice", action="update", target="t2")
    log.log(actor="bob", action="update", target="t1")
    rows = log.query().actor("alice").action("update").target("t1").all()
    assert len(rows) == 1
    assert rows[0].target == "t1"


def test_query_time_window(log):
    log.log(actor="a", action="x", when="2024-01-01T00:00:00+00:00")
    log.log(actor="a", action="y", when="2024-06-01T00:00:00+00:00")
    log.log(actor="a", action="z", when="2024-12-01T00:00:00+00:00")
    rows = (
        log.query()
        .since("2024-03-01T00:00:00+00:00")
        .until("2024-09-01T00:00:00+00:00")
        .all()
    )
    assert [e.action for e in rows] == ["y"]


def test_query_first_and_empty(log):
    assert log.query().actor("nobody").first() is None
    log.log(actor="alice", action="a")
    assert log.query().actor("alice").first().action == "a"


def test_query_is_immutable(log):
    base = log.query().actor("alice")
    base.actor("bob")  # should not mutate base
    log.log(actor="alice", action="a")
    log.log(actor="bob", action="b")
    assert len(base.all()) == 1


def test_query_iterable(log):
    log.log(actor="alice", action="a")
    log.log(actor="alice", action="b")
    actions = [e.action for e in log.query().actor("alice")]
    assert actions == ["a", "b"]


# -- trustworthiness: hash chain -----------------------------------------


def test_verify_clean_log(log):
    log.log(actor="alice", action="a")
    log.log(actor="bob", action="b")
    assert log.verify() is True


def test_verify_detects_tampered_details(log, log_path):
    log.log(actor="alice", action="update", target="s", details={"v": 1})
    log.log(actor="bob", action="update", target="s", details={"v": 2})
    log.close()

    # Tamper directly in SQLite: change a detail without updating the hash.
    conn = sqlite3.connect(log_path)
    conn.execute(
        "UPDATE entries SET details = ? WHERE seq = 1",
        (json.dumps({"v": 999}, sort_keys=True, separators=(",", ":")),),
    )
    conn.commit()
    conn.close()

    al = AuditLog(log_path)
    with pytest.raises(TamperError):
        al.verify()
    al.close()


def test_verify_detects_deleted_row(log, log_path):
    log.log(actor="alice", action="a")
    log.log(actor="bob", action="b")
    log.log(actor="carol", action="c")
    log.close()

    conn = sqlite3.connect(log_path)
    conn.execute("DELETE FROM entries WHERE seq = 2")
    conn.commit()
    conn.close()

    al = AuditLog(log_path)
    with pytest.raises(TamperError):
        al.verify()
    al.close()


def test_verify_detects_rewritten_row(log, log_path):
    log.log(actor="alice", action="a")
    log.log(actor="bob", action="b")
    log.close()

    conn = sqlite3.connect(log_path)
    # Rewrite seq 1's actor but keep its (now wrong) hash.
    conn.execute("UPDATE entries SET actor = 'mallory' WHERE seq = 1")
    conn.commit()
    conn.close()

    al = AuditLog(log_path)
    with pytest.raises(TamperError):
        al.verify()
    al.close()


# -- sensitive data -------------------------------------------------------


def test_redact_default():
    assert redact("super-secret-token") == "<redacted>"


def test_redact_reveal():
    assert redact("4242424242421234", reveal=4) == "<redacted:*1234>"


def test_redact_none():
    assert redact(None) == "<redacted>"


def test_sensitive_value_not_stored(log):
    log.log(actor="alice", action="grant", target="user:42",
            details={"token": redact("hunter2")})
    rows = log.query().actor("alice").all()
    assert rows[0].details == {"token": "<redacted>"}
    # The raw value must not appear anywhere in the file.
    log.close()
    with open(log.path, "rb") as f:
        assert b"hunter2" not in f.read()


# -- module-level API -----------------------------------------------------


def test_module_api_requires_configure():
    # Fresh import state: configure then use.
    import importlib
    importlib.reload(auditlog)
    with pytest.raises(RuntimeError):
        auditlog.query()
    with pytest.raises(RuntimeError):
        auditlog.log(actor="x", action="y")


def test_module_api_roundtrip(tmp_path):
    import importlib
    importlib.reload(auditlog)
    path = str(tmp_path / "default.db")
    auditlog.configure(path)
    auditlog.log(actor="alice", action="update", target="s",
                 details={"k": 1})
    auditlog.log(actor="bob", action="delete", target="s")
    rows = auditlog.query().actor("alice").all()
    assert len(rows) == 1
    assert rows[0].action == "update"
    assert auditlog.query().all()[-1].actor == "bob"


# -- persistence ----------------------------------------------------------


def test_log_persists_across_reopen(log_path):
    al = AuditLog(log_path)
    al.log(actor="alice", action="a")
    al.close()

    al2 = AuditLog(log_path)
    al2.log(actor="bob", action="b")
    rows = al2.query().all()
    assert [e.actor for e in rows] == ["alice", "bob"]
    assert al2.verify() is True
    al2.close()