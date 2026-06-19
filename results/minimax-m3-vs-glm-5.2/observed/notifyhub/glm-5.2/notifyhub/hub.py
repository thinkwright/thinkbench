"""The hub: the one thing callers talk to.

``Hub.notify(recipient, message)`` figures out which channels the
recipient has addresses for, asks each one to deliver, and returns a
:class:`SendReport` describing what happened.  Callers never pick a
channel and never need to change when a new one is added — they just
register it with the hub.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .recipient import Recipient
from .message import Message
from .result import SendReport, SendResult, Status
from .channels.base import Channel


class Hub:
    """A registry of channels and the entry point for sending."""

    def __init__(self, strict: bool = False) -> None:
        self._channels: Dict[str, Channel] = {}
        self.strict = strict

    # -- registration -----------------------------------------------------

    def use(self, channel: Channel) -> "Hub":
        """Register *channel*.  Returns self for chaining."""
        if not channel.name:
            raise ValueError("Channel.name must be set")
        if channel.name in self._channels:
            raise ValueError(f"channel {channel.name!r} is already registered")
        self._channels[channel.name] = channel
        return self

    def channel(self, name: str) -> Optional[Channel]:
        return self._channels.get(name)

    @property
    def channels(self) -> List[str]:
        return list(self._channels)

    # -- sending ----------------------------------------------------------

    def notify(self, recipient: Recipient, message: Message) -> SendReport:
        """Deliver *message* to *recipient* via every channel it can.

        Returns a :class:`SendReport` with one :class:`SendResult` per
        registered channel: ``OK`` on success, ``FAILED`` (with the
        error) on a raised exception, or ``SKIPPED`` when the recipient
        has no address for that channel.

        In ``strict`` mode, the first failure raises immediately and no
        further channels are tried.
        """
        report = SendReport()
        for name, channel in self._channels.items():
            if not recipient.has(name):
                report.add(SendResult(name, Status.SKIPPED, detail="no address"))
                continue
            try:
                channel.send(recipient, message)
                report.add(SendResult(name, Status.OK))
            except BaseException as exc:  # noqa: BLE001 - we want to see *all* failures
                result = SendResult(name, Status.FAILED, error=exc, detail=str(exc))
                report.add(result)
                if self.strict:
                    raise
        return report