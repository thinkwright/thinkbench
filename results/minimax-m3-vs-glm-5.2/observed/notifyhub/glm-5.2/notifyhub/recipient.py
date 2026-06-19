"""A person we want to reach.

A :class:`Recipient` is an identifier plus a bag of channel addresses.
Callers never decide *how* a message gets delivered — they hand over a
recipient and the hub uses whichever channels it knows about for which
this recipient has an address.  Adding a new way to reach people later
means registering a new channel with the hub, not touching call sites.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional


class Recipient:
    """Someone to send things to.

    Parameters
    ----------
    id:
        A stable, human-meaningful identifier for the person (e.g. a
        username or customer id).  Used for logging and routing only.
    **addresses:
        Channel addresses, keyed by channel name.  For example
        ``email="alice@example.com"``, ``phone="+15551234567"``,
        ``webhook="https://hooks.example.com/u/alice"``.  The keys are
        whatever the registered channels look for; unknown keys are
        simply ignored by channels that don't claim them.
    """

    __slots__ = ("id", "addresses")

    def __init__(self, id: str, **addresses: str) -> None:
        if not id:
            raise ValueError("Recipient.id must be a non-empty string")
        self.id = id
        # Copy so callers can't mutate our state after the fact.
        self.addresses = dict(addresses)

    def address_for(self, channel: str) -> Optional[str]:
        """Return the address for *channel*, or ``None`` if unset."""
        return self.addresses.get(channel)

    def has(self, channel: str) -> bool:
        """True if this recipient has an address for *channel*."""
        return channel in self.addresses

    def channels(self) -> Iterable[str]:
        """The channel names this recipient has addresses for."""
        return self.addresses.keys()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        addrs = ", ".join(f"{k}={v!r}" for k, v in self.addresses.items())
        return f"Recipient(id={self.id!r}{', ' + addrs if addrs else ''})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Recipient):
            return NotImplemented
        return self.id == other.id and self.addresses == other.addresses

    def __hash__(self) -> int:
        return hash((self.id, tuple(sorted(self.addresses.items()))))