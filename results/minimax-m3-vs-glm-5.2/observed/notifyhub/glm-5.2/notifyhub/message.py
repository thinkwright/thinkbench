"""What we want to say to someone.

A :class:`Message` carries a short ``subject`` and a ``body`` — the two
things every channel can render — plus optional structured ``data`` for
channels that can do something richer with it, and free-form ``meta`` for
callers who want to thread their own context (correlation ids, etc.)
through to channels and logs.

It is deliberately not a bag of channel-specific knobs.  If a channel
needs configuration, that configuration lives on the channel, not on
every message.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


class Message:
    __slots__ = ("subject", "body", "data", "meta")

    def __init__(
        self,
        subject: str,
        body: str = "",
        *,
        data: Optional[Mapping[str, Any]] = None,
        meta: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if subject is None:
            raise ValueError("Message.subject is required")
        self.subject = subject
        self.body = body
        # Shallow-copy mappings so callers can't mutate them post-hoc.
        self.data = dict(data) if data else {}
        self.meta = dict(meta) if meta else {}

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"Message(subject={self.subject!r}, body={self.body!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Message):
            return NotImplemented
        return (
            self.subject == other.subject
            and self.body == other.body
            and self.data == other.data
            and self.meta == other.meta
        )