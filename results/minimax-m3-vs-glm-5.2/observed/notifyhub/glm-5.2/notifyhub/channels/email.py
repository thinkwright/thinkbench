"""Email channel.

This pass doesn't wire up a real SMTP provider.  By default an
:class:`EmailChannel` *records* the messages it would send on a list you
can inspect (handy for tests and local dev).  If you point it at a real
``smtplib.SMTP``-shaped object it will actually send.

The address key is ``email``.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Callable, List, Optional

from .base import Channel
from ..recipient import Recipient
from ..message import Message


# A factory that returns an SMTP-like object.  Returning the object (rather
# than holding one open) lets the channel open a fresh connection per send,
# which is what you want for low-volume notifications.
SMTPFactory = Callable[[], "smtplib.SMTP"]


class EmailChannel(Channel):
    name = "email"

    def __init__(
        self,
        sender: str = "notifyhub@localhost",
        smtp_factory: Optional[SMTPFactory] = None,
    ) -> None:
        self.sender = sender
        self._smtp_factory = smtp_factory
        # When no real provider is configured, sent messages land here so
        # they're visible instead of vanishing.
        self.outbox: List[EmailMessage] = []

    def _build(self, recipient: Recipient, message: Message) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = recipient.address_for(self.name)  # type: ignore[assignment]
        msg["Subject"] = message.subject
        msg.set_content(message.body)
        return msg

    def send(self, recipient: Recipient, message: Message) -> None:
        to = recipient.address_for(self.name)
        if not to:
            raise ValueError(f"recipient {recipient.id!r} has no email address")
        msg = self._build(recipient, message)

        if self._smtp_factory is None:
            # Stand-in: record it so callers can see what would have gone.
            self.outbox.append(msg)
            return

        with self._smtp_factory() as smtp:
            smtp.send_message(msg)