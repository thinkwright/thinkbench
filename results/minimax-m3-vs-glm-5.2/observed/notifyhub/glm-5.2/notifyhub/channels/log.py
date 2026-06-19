"""A channel that just writes a line to a logger.

Useful in development and as a default that's always safe.  It writes to
the ``notifyhub.log`` logger by default; pass any ``logging.Logger`` to
point it elsewhere.
"""

from __future__ import annotations

import logging

from .base import Channel
from ..recipient import Recipient
from ..message import Message


class LogChannel(Channel):
    name = "log"

    def __init__(self, logger: logging.Logger | None = None, level: int = logging.INFO) -> None:
        self._logger = logger or logging.getLogger("notifyhub.log")
        self._level = level

    def send(self, recipient: Recipient, message: Message) -> None:
        self._logger.log(
            self._level,
            "notifyhub -> %s [%s] %s | %s",
            recipient.id,
            self.name,
            message.subject,
            message.body,
        )