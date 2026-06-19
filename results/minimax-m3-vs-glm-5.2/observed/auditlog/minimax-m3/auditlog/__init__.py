"""auditlog — a small, trustworthy audit log.

Records events (who did what, when, to what) to a SQLite database and lets
you query them back. Entries are append-only and hash-chained so tampering
is detectable; with an optional HMAC secret, even the file itself can be
verified.

Typical use:

    from auditlog import AuditLog, redact

    log = AuditLog("audit.db")
    log.record(actor="alice", action="update", target="user:42",
               details={"role": "admin"})
    log.record(actor="alice", action="update", target="user:42",
               details={"password": redact("hunter2")})

    for entry in log.query(actor="alice"):
        print(entry)
"""

from .core import AuditLog, Entry, redact, verify_chain

__all__ = ["AuditLog", "Entry", "redact", "verify_chain"]
__version__ = "0.1.0"
