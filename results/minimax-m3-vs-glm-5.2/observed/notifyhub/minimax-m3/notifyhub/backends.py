"""Backends: the things that actually put a message somewhere.

The email/sms/webhook backends here are recording stubs — they don't talk to a
real provider, but they capture exactly what would have been sent so callers
can verify intent and tests can assert on it. Flip `fail_with` to make them
raise, which is how we exercise the failure path.

The log backend writes through the stdlib logging module, which is real.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from notifyhub.errors import DeliveryError
from notifyhub.message import Message
from notifyhub.recipient import Channel, ChannelKind, Recipient


class Backend:
    """Base class. Subclasses implement `deliver`."""

    kind: ChannelKind  # set by subclass

    def deliver(self, recipient: Recipient, channel: Channel, message: Message) -> None:
        raise NotImplementedError


@dataclass
class EmailBackend(Backend):
    """Stub email backend. Records what it would have sent.

    Replace with an SES/Sendgrid/SMTP implementation when ready — the interface
    is `deliver(recipient, channel, message) -> None`, raise DeliveryError on
    failure.
    """

    kind: ChannelKind = ChannelKind.EMAIL
    sent: list[dict[str, Any]] = field(default_factory=list)
    fail_with: Optional[Exception] = None

    def deliver(self, recipient: Recipient, channel: Channel, message: Message) -> None:
        if self.fail_with is not None:
            raise DeliveryError(
                str(self.fail_with) or "email delivery failed",
                recipient=recipient,
                channel=channel,
                cause=self.fail_with,
            )
        self.sent.append(
            {
                "to": channel.address,
                "recipient_name": recipient.name,
                "subject": message.subject,
                "body": message.body,
                "metadata": dict(message.metadata),
            }
        )


@dataclass
class SmsBackend(Backend):
    """Stub SMS backend. Records what it would have sent."""

    kind: ChannelKind = ChannelKind.SMS
    sent: list[dict[str, Any]] = field(default_factory=list)
    fail_with: Optional[Exception] = None

    def deliver(self, recipient: Recipient, channel: Channel, message: Message) -> None:
        if self.fail_with is not None:
            raise DeliveryError(
                str(self.fail_with) or "sms delivery failed",
                recipient=recipient,
                channel=channel,
                cause=self.fail_with,
            )
        # SMS has no subject; collapse subject+body into one short string.
        text = message.body or message.subject
        self.sent.append(
            {
                "to": channel.address,
                "recipient_name": recipient.name,
                "text": text,
                "metadata": dict(message.metadata),
            }
        )


@dataclass
class WebhookBackend(Backend):
    """Stub webhook backend. Records what it would have POSTed."""

    kind: ChannelKind = ChannelKind.WEBHOOK
    sent: list[dict[str, Any]] = field(default_factory=list)
    fail_with: Optional[Exception] = None

    def deliver(self, recipient: Recipient, channel: Channel, message: Message) -> None:
        if self.fail_with is not None:
            raise DeliveryError(
                str(self.fail_with) or "webhook delivery failed",
                recipient=recipient,
                channel=channel,
                cause=self.fail_with,
            )
        payload = {
            "recipient": {"name": recipient.name, "address": channel.address},
            "subject": message.subject,
            "body": message.body,
            "metadata": dict(message.metadata),
        }
        self.sent.append({"url": channel.address, "payload": payload})


@dataclass
class LogBackend(Backend):
    """Real backend: writes to a Python logger.

    `logger_name` lets you route different recipients to different loggers.
    Defaults to "notifyhub.log".
    """

    kind: ChannelKind = ChannelKind.LOG
    logger_name: str = "notifyhub.log"
    level: int = logging.INFO

    def deliver(self, recipient: Recipient, channel: Channel, message: Message) -> None:
        logger = logging.getLogger(self.logger_name)
        # `address` for a LOG channel is treated as a logger name override.
        if channel.address:
            logger = logging.getLogger(channel.address)
        record = {
            "recipient": recipient.name,
            "subject": message.subject,
            "body": message.body,
            "metadata": dict(message.metadata),
        }
        logger.log(self.level, "notifyhub: %s", json.dumps(record, default=str))
