"""Who we want to reach and how."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class ChannelKind(str, Enum):
    EMAIL = "email"
    SMS = "sms"
    WEBHOOK = "webhook"
    LOG = "log"


@dataclass(frozen=True)
class Channel:
    """One way to reach a person.

    `kind` decides which backend handles it. `address` is whatever the backend
    needs (email address, phone number, webhook URL, or a logger name for LOG).
    `priority` lets callers express preference — lower numbers are tried first.
    """

    kind: ChannelKind
    address: str
    priority: int = 100

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ChannelKind):
            raise TypeError(f"kind must be a ChannelKind, got {self.kind!r}")
        if not isinstance(self.address, str) or not self.address.strip():
            raise ValueError("address must be a non-empty string")


@dataclass(frozen=True)
class Recipient:
    """A person we want to notify, with the channels we can reach them on."""

    name: str
    channels: tuple[Channel, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must be a non-empty string")
        if not isinstance(self.channels, tuple):
            # accept any iterable, normalize to tuple
            object.__setattr__(self, "channels", tuple(self.channels))
        if not self.channels:
            raise ValueError("Recipient needs at least one Channel")

    @classmethod
    def of(cls, name: str, *channels: Channel) -> "Recipient":
        return cls(name=name, channels=channels)

    def ordered(self) -> tuple[Channel, ...]:
        """Channels sorted by priority (lowest first), stable on ties."""
        return tuple(sorted(self.channels, key=lambda c: (c.priority, c.kind.value)))
