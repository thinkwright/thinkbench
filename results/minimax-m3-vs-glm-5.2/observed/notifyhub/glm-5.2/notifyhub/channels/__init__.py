"""Channels: the things that actually deliver a message.

A channel is anything with a ``name`` and a ``send(recipient, message)``
method.  ``send`` should return ``None`` on success and raise on failure —
the hub turns raised exceptions into :class:`SendResult` failures so
callers can see them.  Channels look up their own address on the
recipient via ``recipient.address_for(self.name)`` and decide for
themselves whether to send.
"""

from .base import Channel
from .log import LogChannel
from .email import EmailChannel
from .sms import SMSChannel
from .webhook import WebhookChannel

__all__ = ["Channel", "LogChannel", "EmailChannel", "SMSChannel", "WebhookChannel"]