"""notifyhub — one call reaches a person however we reach them.

The public surface is intentionally small::

    import notifyhub

    hub = notifyhub.Hub()
    hub.use(notifyhub.LogChannel())

    alice = notifyhub.Recipient("alice", email="alice@example.com")
    hub.notify(alice, notifyhub.Message("Hello", "from notifyhub"))

A single ``notify`` call fans out to every channel the recipient has an
address for.  Each attempt produces a :class:`SendResult` that the caller
can inspect; nothing fails quietly.
"""

from .recipient import Recipient
from .message import Message
from .result import SendResult, SendReport, Status
from .channels.base import Channel
from .channels.log import LogChannel
from .channels.email import EmailChannel
from .channels.sms import SMSChannel
from .channels.webhook import WebhookChannel
from .hub import Hub

__all__ = [
    "Recipient",
    "Message",
    "SendResult",
    "SendReport",
    "Status",
    "Channel",
    "LogChannel",
    "EmailChannel",
    "SMSChannel",
    "WebhookChannel",
    "Hub",
]

__version__ = "0.1.0"