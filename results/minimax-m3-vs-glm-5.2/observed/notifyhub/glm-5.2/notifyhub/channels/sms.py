"""SMS channel.

No real SMS provider in this pass.  Like the email channel, an
:class:`SMSChannel` records what it would have sent on ``outbox`` so the
delivery is visible rather than silent.  Swap in a real provider by
passing a ``sender`` callable ``(to, text) -> None`` that raises on
failure.

The address key is ``phone``.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from .base import Channel
from ..recipient import Recipient
from ..message import Message


SMSSender = Callable[[str, str], None]


class SMSChannel(Channel):
    name = "phone"

    def __init__(self, sender: Optional[SMSSender] = None) -> None:
        self._sender = sender
        self.outbox: List[Tuple[str, str]] = []

    def send(self, recipient: Recipient, message: Message) -> None:
        to = recipient.address_for(self.name)
        if not to:
            raise ValueError(f"recipient {recipient.id!r} has no phone number")
        text = f"{message.subject}: {message.body}".strip()

        if self._sender is None:
            self.outbox.append((to, text))
            return

        self._sender(to, text)