"""Errors that mean something."""

from __future__ import annotations

from typing import Optional

from notifyhub.recipient import Channel, Recipient


class DeliveryError(Exception):
    """A channel accepted the message but failed to deliver it.

    Carries enough context to log, retry, or alert on without re-deriving it.
    """

    def __init__(
        self,
        message: str,
        *,
        recipient: Recipient,
        channel: Channel,
        cause: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.recipient = recipient
        self.channel = channel
        self.cause = cause

    def __str__(self) -> str:
        base = (
            f"failed to deliver to {self.recipient.name!r} "
            f"via {self.channel.kind.value} ({self.channel.address!r}): {self.message}"
        )
        if self.cause is not None:
            base += f" [caused by {type(self.cause).__name__}: {self.cause}]"
        return base


class NoReachableChannel(Exception):
    """Every channel we tried refused or errored. Nothing got through."""

    def __init__(self, recipient: Recipient, attempts: list[DeliveryError]) -> None:
        super().__init__(
            f"no reachable channel for {recipient.name!r} "
            f"after {len(attempts)} attempt(s)"
        )
        self.recipient = recipient
        self.attempts = attempts
