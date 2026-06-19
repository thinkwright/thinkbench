"""auditlog — a small, trustworthy audit log.

Record who did what, and when; query it back later. Entries are append-only
and chained together with a hash so that tampering with the log file is
detectable.

Example
-------
    import auditlog

    auditlog.configure("audit.log")
    auditlog.log(actor="alice", action="update_setting",
                 target="setting:timeout", details={"old": 30, "new": 60})

    for entry in auditlog.query().actor("alice").all():
        print(entry.when, entry.action, entry.target)

See README.md for more.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Mapping, Optional

__all__ = [
    "Entry",
    "AuditLog",
    "Query",
    "configure",
    "log",
    "query",
    "redact",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    id         TEXT    NOT NULL UNIQUE,
    when_iso   TEXT    NOT NULL,
    actor      TEXT    NOT NULL,
    action     TEXT    NOT NULL,
    target     TEXT,
    details    TEXT    NOT NULL,
    prev_hash  TEXT    NOT NULL,
    hash       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_actor  ON entries (actor);
CREATE INDEX IF NOT EXISTS idx_target ON entries (target);
CREATE INDEX IF NOT EXISTS idx_when   ON entries (when_iso);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _hash(prev_hash: str, payload: Mapping[str, Any]) -> str:
    h = hashlib.sha256()
    h.update(prev_hash.encode("utf-8"))
    h.update(b"\n")
    h.update(_canonical(payload).encode("utf-8"))
    return h.hexdigest()


def redact(value: Any, reveal: int = 0) -> str:
    """Return a placeholder string marking `value` as redacted.

    Use this inside `details` when you want to record that a sensitive value
    existed without storing the value itself. `reveal` keeps that many
    trailing characters visible (e.g. the last 4 of an account number).
    """
    s = "" if value is None else str(value)
    if reveal and len(s) > reveal:
        return f"<redacted:*{s[-reveal:]}>"
    return "<redacted>"


@dataclass(frozen=True)
class Entry:
    """A single audit-log record, as read back from the log."""

    seq: int
    id: str
    when: str
    actor: str
    action: str
    target: Optional[str]
    details: dict
    prev_hash: str
    hash: str

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"Entry(seq={self.seq}, when={self.when}, actor={self.actor!r}, "
            f"action={self.action!r}, target={self.target!r})"
        )


class TamperError(Exception):
    """Raised when the log's hash chain does not verify."""


class AuditLog:
    """An append-only, hash-chained audit log backed by a SQLite file.

    Each entry's hash covers the previous entry's hash plus a canonical
    encoding of the entry's payload, so inserting, deleting, or rewriting a
    row breaks the chain and is caught by `verify()`.
    """

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "AuditLog":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- writing -----------------------------------------------------------

    def log(
        self,
        *,
        actor: str,
        action: str,
        target: Optional[str] = None,
        details: Optional[Mapping[str, Any]] = None,
        when: Optional[str] = None,
    ) -> Entry:
        """Append one entry to the log and return it.

        `actor` is who did the thing, `action` is what they did, `target` is
        the thing it was done to (may be None), and `details` is any extra
        context. Sensitive values in `details` should be passed through
        `redact()` first.
        """
        if not actor or not action:
            raise ValueError("actor and action are required")
        details = dict(details) if details else {}
        when_iso = when or _now_iso()

        with self._lock:
            row = self._conn.execute(
                "SELECT hash FROM entries ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = row["hash"] if row else "GENESIS"
            seq_row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS n FROM entries"
            ).fetchone()
            seq = seq_row["n"] + 1
            entry_id = f"{seq:010d}"

            payload = {
                "id": entry_id,
                "when": when_iso,
                "actor": actor,
                "action": action,
                "target": target,
                "details": details,
            }
            h = _hash(prev_hash, payload)
            self._conn.execute(
                "INSERT INTO entries "
                "(id, when_iso, actor, action, target, details, prev_hash, hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry_id,
                    when_iso,
                    actor,
                    action,
                    target,
                    _canonical(details),
                    prev_hash,
                    h,
                ),
            )
            self._conn.commit()
            return Entry(
                seq=seq,
                id=entry_id,
                when=when_iso,
                actor=actor,
                action=action,
                target=target,
                details=details,
                prev_hash=prev_hash,
                hash=h,
            )

    # -- reading -----------------------------------------------------------

    def query(self) -> "Query":
        return Query(self)

    def _fetch(self, where: str, params: Iterable[Any]) -> list[Entry]:
        sql = (
            "SELECT seq, id, when_iso, actor, action, target, details, "
            "prev_hash, hash FROM entries"
        )
        if where:
            sql += f" WHERE {where}"
        sql += " ORDER BY seq ASC"
        rows = self._conn.execute(sql, list(params)).fetchall()
        return [
            Entry(
                seq=r["seq"],
                id=r["id"],
                when=r["when_iso"],
                actor=r["actor"],
                action=r["action"],
                target=r["target"],
                details=json.loads(r["details"]),
                prev_hash=r["prev_hash"],
                hash=r["hash"],
            )
            for r in rows
        ]

    def verify(self) -> bool:
        """Walk the hash chain and confirm every entry is intact.

        Raises `TamperError` with the offending seq number on the first
        mismatch. Returns True if the whole log checks out.
        """
        prev = "GENESIS"
        for r in self._conn.execute(
            "SELECT seq, prev_hash, hash, id, when_iso, actor, action, target, details "
            "FROM entries ORDER BY seq ASC"
        ).fetchall():
            if r["prev_hash"] != prev:
                raise TamperError(f"broken chain at seq {r['seq']} (prev_hash)")
            payload = {
                "id": r["id"],
                "when": r["when_iso"],
                "actor": r["actor"],
                "action": r["action"],
                "target": r["target"],
                "details": json.loads(r["details"]),
            }
            expected = _hash(prev, payload)
            if not hmac.compare_digest(expected, r["hash"]):
                raise TamperError(f"hash mismatch at seq {r['seq']}")
            prev = r["hash"]
        return True


class Query:
    """A fluent, immutable query builder over the log.

        log.query().actor("alice").target("setting:timeout").all()
    """

    def __init__(self, store: AuditLog, clauses: Optional[list[tuple[str, Any]]] = None):
        self._store = store
        self._clauses = clauses or []

    def _clone(self, clause: tuple[str, Any]) -> "Query":
        return Query(self._store, self._clauses + [clause])

    def actor(self, name: str) -> "Query":
        return self._clone(("actor = ?", name))

    def action(self, name: str) -> "Query":
        return self._clone(("action = ?", name))

    def target(self, name: str) -> "Query":
        return self._clone(("target = ?", name))

    def since(self, when_iso: str) -> "Query":
        return self._clone(("when_iso >= ?", when_iso))

    def until(self, when_iso: str) -> "Query":
        return self._clone(("when_iso <= ?", when_iso))

    def _build(self) -> tuple[str, list[Any]]:
        if not self._clauses:
            return "", []
        where = " AND ".join(c for c, _ in self._clauses)
        params = [p for _, p in self._clauses]
        return where, params

    def all(self) -> list[Entry]:
        where, params = self._build()
        return self._store._fetch(where, params)

    def first(self) -> Optional[Entry]:
        rows = self.all()
        return rows[0] if rows else None

    def __iter__(self) -> Iterator[Entry]:
        return iter(self.all())


# -- module-level convenience API -----------------------------------------

_default: Optional[AuditLog] = None


def configure(path: str) -> AuditLog:
    """Set the default log used by `log()` and `query()`."""
    global _default
    if _default is not None:
        _default.close()
    _default = AuditLog(path)
    return _default


def _require_default() -> AuditLog:
    if _default is None:
        raise RuntimeError(
            "no default log configured; call auditlog.configure(path) first"
        )
    return _default


def log(
    *,
    actor: str,
    action: str,
    target: Optional[str] = None,
    details: Optional[Mapping[str, Any]] = None,
    when: Optional[str] = None,
) -> Entry:
    """Append to the default log. See `AuditLog.log`."""
    return _require_default().log(
        actor=actor, action=action, target=target, details=details, when=when
    )


def query() -> Query:
    """Start a query against the default log."""
    return _require_default().query()