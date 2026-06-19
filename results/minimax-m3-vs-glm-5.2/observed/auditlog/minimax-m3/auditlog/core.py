"""Core implementation of the audit log.

Design notes
------------
* **Storage**: SQLite (stdlib). One table, append-only. We never expose
  update or delete; the only way to "remove" an entry is to drop the file.
* **Trust**: Every entry stores a SHA-256 hash of (prev_hash || canonical
  entry). This forms a hash chain — modifying any past entry breaks every
  subsequent hash. With an optional HMAC secret, the hash itself is signed,
  so an attacker who can rewrite the file but not the secret cannot forge
  a consistent chain.
* **Sensitivity**: `details` is stored as JSON. Callers mark sensitive
  values with `redact()`; the stored value is the placeholder string
  "[REDACTED]" and a `redacted=True` flag is set on the entry.
* **Ease of use**: `record()` takes plain kwargs. `query()` takes the same
  fields as filters. `audit()` is a context manager / decorator for the
  common "actor does action on target" shape.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import hmac
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Iterator, Optional


REDACTED = "[REDACTED]"


# ---------------------------------------------------------------------------
# Public value types
# ---------------------------------------------------------------------------


class _Redacted:
    """Sentinel wrapper. Anything wrapped in this is stored as [REDACTED]."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "redact(...)"


def redact(value: Any) -> Any:
    """Mark a value as sensitive. Stored as the literal string "[REDACTED]"."""
    # Accept the value but discard it — we don't want it lingering in memory
    # longer than necessary, and we definitely don't want it on disk.
    del value
    return _Redacted()


@dataclass(frozen=True)
class Entry:
    """A single audit log entry, as returned by query()."""

    id: str
    timestamp: float            # Unix seconds, float
    actor: str
    action: str
    target: Optional[str]
    details: dict
    redacted: bool
    prev_hash: str
    hash: str
    sequence: int               # Monotonic per-log ordinal (1, 2, 3, ...)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _canonical(obj: Any) -> str:
    """Stable JSON encoding. Sort keys, no whitespace, ensure_ascii=False
    so non-ASCII names round-trip cleanly."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def _hash_entry(prev_hash: str, payload: dict) -> str:
    """SHA-256 of (prev_hash || canonical payload). Hex digest."""
    h = hashlib.sha256()
    h.update(prev_hash.encode("utf-8"))
    h.update(b"\x00")
    h.update(_canonical(payload).encode("utf-8"))
    return h.hexdigest()


def _sign(hash_hex: str, secret: bytes) -> str:
    return hmac.new(secret, hash_hex.encode("utf-8"), hashlib.sha256).hexdigest()


def _scrub(value: Any) -> tuple[Any, bool]:
    """Walk a JSON-like value, replacing _Redacted sentinels with REDACTED.

    Returns (clean_value, any_redacted).
    """
    redacted = False

    if isinstance(value, _Redacted):
        return REDACTED, True

    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            cv, r = _scrub(v)
            out[k] = cv
            redacted = redacted or r
        return out, redacted

    if isinstance(value, (list, tuple)):
        out_list = []
        for v in value:
            cv, r = _scrub(v)
            out_list.append(cv)
            redacted = redacted or r
        return out_list, redacted

    return value, False


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------


_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    sequence    INTEGER PRIMARY KEY AUTOINCREMENT,
    id          TEXT    NOT NULL UNIQUE,
    timestamp   REAL    NOT NULL,
    actor       TEXT    NOT NULL,
    action      TEXT    NOT NULL,
    target      TEXT,
    details     TEXT    NOT NULL,
    redacted    INTEGER NOT NULL,
    prev_hash   TEXT    NOT NULL,
    hash        TEXT    NOT NULL,
    signature   TEXT
);
CREATE INDEX IF NOT EXISTS idx_entries_actor   ON entries(actor);
CREATE INDEX IF NOT EXISTS idx_entries_action  ON entries(action);
CREATE INDEX IF NOT EXISTS idx_entries_target  ON entries(target);
CREATE INDEX IF NOT EXISTS idx_entries_time    ON entries(timestamp);
"""


class AuditLog:
    """An audit log backed by a SQLite file.

    Parameters
    ----------
    path:
        Path to the SQLite database file. Created if it doesn't exist.
        Use ":memory:" for an in-memory log (useful in tests).
    secret:
        Optional HMAC secret. If provided, every entry's hash is also
        signed, and `verify_chain()` will check the signature. Keep this
        secret out of band from the log file itself.
    """

    def __init__(self, path: str, *, secret: Optional[bytes] = None):
        self.path = path
        self.secret = secret
        self._conn = sqlite3.connect(path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)

    # -- writing -------------------------------------------------------

    def record(
        self,
        *,
        actor: str,
        action: str,
        target: Optional[str] = None,
        details: Optional[dict] = None,
        timestamp: Optional[float] = None,
    ) -> Entry:
        """Append one entry. Returns the stored Entry.

        `actor` and `action` are required. `target` is a free-form string
        (e.g. "user:42", "settings.billing"). `details` is a dict of
        arbitrary JSON-serialisable context; wrap sensitive values in
        `redact()`.
        """
        if not actor:
            raise ValueError("actor is required")
        if not action:
            raise ValueError("action is required")

        details_in = details or {}
        clean_details, any_redacted = _scrub(details_in)

        ts = timestamp if timestamp is not None else time.time()
        entry_id = uuid.uuid4().hex

        # Read the previous hash inside the same statement so concurrent
        # writers can't interleave and break the chain. SQLite serialises
        # writes, so this is safe.
        row = self._conn.execute(
            "SELECT hash FROM entries ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        prev_hash = row["hash"] if row else "0" * 64

        payload = {
            "id": entry_id,
            "timestamp": ts,
            "actor": actor,
            "action": action,
            "target": target,
            "details": clean_details,
            "redacted": any_redacted,
            "prev_hash": prev_hash,
        }
        hash_hex = _hash_entry(prev_hash, payload)
        signature = _sign(hash_hex, self.secret) if self.secret else None

        cur = self._conn.execute(
            """
            INSERT INTO entries
                (id, timestamp, actor, action, target, details,
                 redacted, prev_hash, hash, signature)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                ts,
                actor,
                action,
                target,
                _canonical(clean_details),
                1 if any_redacted else 0,
                prev_hash,
                hash_hex,
                signature,
            ),
        )
        sequence = cur.lastrowid
        return Entry(
            id=entry_id,
            timestamp=ts,
            actor=actor,
            action=action,
            target=target,
            details=clean_details,
            redacted=any_redacted,
            prev_hash=prev_hash,
            hash=hash_hex,
            sequence=sequence,
        )

    @contextlib.contextmanager
    def audit(
        self,
        *,
        actor: str,
        action: str,
        target: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> Iterator[dict]:
        """Context manager that records an entry on exit.

        Usage:

            with log.audit(actor="alice", action="delete", target="user:42") as ctx:
                do_the_delete()
                ctx["details"] = {"rows": 3}

        The yielded dict's contents are merged into `details` on exit.
        If the block raises, the entry is still recorded but with
        `details["error"]` set to the exception class name and the
        exception is re-raised.
        """
        merged: dict = dict(details or {})
        try:
            yield merged
        except BaseException as exc:
            merged.setdefault("error", type(exc).__name__)
            merged.setdefault("ok", False)
            self.record(actor=actor, action=action, target=target, details=merged)
            raise
        else:
            merged.setdefault("ok", True)
            self.record(actor=actor, action=action, target=target, details=merged)

    def audit_call(
        self,
        *,
        actor: str,
        action: str,
        target: Optional[str] = None,
    ):
        """Decorator form of `audit()`. The decorated function's return
        value is recorded in `details["result"]` (truncated to a string).
        """
        def deco(fn):
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                with self.audit(actor=actor, action=action, target=target) as ctx:
                    result = fn(*args, **kwargs)
                    ctx["result"] = repr(result)[:200]
                return result
            return wrapper
        return deco

    # -- reading -------------------------------------------------------

    def query(
        self,
        *,
        actor: Optional[str] = None,
        action: Optional[str] = None,
        target: Optional[str] = None,
        since: Optional[float] = None,
        until: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> list[Entry]:
        """Return entries matching the given filters, newest first."""
        clauses = []
        params: list[Any] = []
        if actor is not None:
            clauses.append("actor = ?"); params.append(actor)
        if action is not None:
            clauses.append("action = ?"); params.append(action)
        if target is not None:
            # Exact match by default. Callers wanting prefix matches can
            # post-filter; we keep the SQL simple and predictable.
            clauses.append("target = ?"); params.append(target)
        if since is not None:
            clauses.append("timestamp >= ?"); params.append(since)
        if until is not None:
            clauses.append("timestamp <= ?"); params.append(until)

        sql = "SELECT * FROM entries"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY sequence DESC"
        if limit is not None:
            sql += " LIMIT ?"; params.append(int(limit))

        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_entry(r) for r in rows]

    def get(self, entry_id: str) -> Optional[Entry]:
        row = self._conn.execute(
            "SELECT * FROM entries WHERE id = ?", (entry_id,)
        ).fetchone()
        return _row_to_entry(row) if row else None

    def __iter__(self) -> Iterator[Entry]:
        """Iterate all entries, oldest first (chronological order)."""
        rows = self._conn.execute(
            "SELECT * FROM entries ORDER BY sequence ASC"
        ).fetchall()
        return iter(_row_to_entry(r) for r in rows)

    def __len__(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM entries").fetchone()
        return int(row["n"])

    # -- integrity -----------------------------------------------------

    def verify_chain(self) -> tuple[bool, Optional[str]]:
        """Walk the chain and confirm every hash matches.

        Returns (ok, message). If a secret was set, signatures are also
        checked. The first broken entry's sequence number is reported.
        """
        return verify_chain(self.path, secret=self.secret)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "AuditLog":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _row_to_entry(row: sqlite3.Row) -> Entry:
    return Entry(
        id=row["id"],
        timestamp=row["timestamp"],
        actor=row["actor"],
        action=row["action"],
        target=row["target"],
        details=json.loads(row["details"]),
        redacted=bool(row["redacted"]),
        prev_hash=row["prev_hash"],
        hash=row["hash"],
        sequence=row["sequence"],
    )


def verify_chain(
    path: str, *, secret: Optional[bytes] = None
) -> tuple[bool, Optional[str]]:
    """Verify the hash chain of a log file. Opens it read-only.

    Returns (True, None) on success, or (False, "explanation") on the
    first inconsistency found.
    """
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT * FROM entries ORDER BY sequence ASC"
        ).fetchall()
    finally:
        conn.close()

    prev_hash = "0" * 64
    expected_seq = 1
    for row in rows:
        if row["sequence"] != expected_seq:
            return False, f"sequence gap at {row['sequence']}"
        expected_seq += 1

        if row["prev_hash"] != prev_hash:
            return False, f"prev_hash mismatch at sequence {row['sequence']}"

        payload = {
            "id": row["id"],
            "timestamp": row["timestamp"],
            "actor": row["actor"],
            "action": row["action"],
            "target": row["target"],
            "details": json.loads(row["details"]),
            "redacted": bool(row["redacted"]),
            "prev_hash": row["prev_hash"],
        }
        expected_hash = _hash_entry(row["prev_hash"], payload)
        if not hmac.compare_digest(expected_hash, row["hash"]):
            return False, f"hash mismatch at sequence {row['sequence']}"

        if secret is not None:
            if row["signature"] is None:
                return False, f"missing signature at sequence {row['sequence']}"
            expected_sig = _sign(row["hash"], secret)
            if not hmac.compare_digest(expected_sig, row["signature"]):
                return False, f"bad signature at sequence {row['sequence']}"

        prev_hash = row["hash"]

    return True, None
