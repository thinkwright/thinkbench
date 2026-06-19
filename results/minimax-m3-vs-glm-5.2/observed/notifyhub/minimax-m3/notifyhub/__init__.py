"""notifyhub — deliver a message to a person through whatever channel reaches them."""

from notifyhub.message import Message
from notifyhub.recipient import Channel, Recipient
from notifyhub.notifier import Notifier, send
from notifyhub.errors import DeliveryError, NoReachableChannel
from notifyhub.backends import (
    Backend,
    EmailBackend,
    SmsBackend,
    WebhookBackend,
    LogBackend,
)

__all__ = [
    "Message",
    "Channel",
    "Recipient",
    "Notifier",
    "send",
    "DeliveryError",
    "NoReachableChannel",
    "Backend",
    "EmailBackend",
    "SmsBackend",
    "WebhookBackend",
    "LogBackend",
]
