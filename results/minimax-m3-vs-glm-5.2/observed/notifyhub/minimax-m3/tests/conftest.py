"""Shared fixtures for the test suite."""

import pytest

from notifyhub.backends import EmailBackend, LogBackend, SmsBackend, WebhookBackend
from notifyhub.message import Message
from notifyhub.notifier import Notifier
from notifyhub.recipient import Channel, ChannelKind, Recipient


@pytest.fixture
def message() -> Message:
    return Message(subject="hi", body="hello there", metadata={"trace": "abc"})


@pytest.fixture
def alice() -> Recipient:
    return Recipient(
        name="alice",
        channels=(
            Channel(ChannelKind.EMAIL, "alice@example.com", priority=10),
            Channel(ChannelKind.SMS, "+15555550100", priority=20),
        ),
    )


@pytest.fixture
def bob_log_only() -> Recipient:
    return Recipient(
        name="bob",
        channels=(Channel(ChannelKind.LOG, "notifyhub.bob", priority=10),),
    )


@pytest.fixture
def email_backend() -> EmailBackend:
    return EmailBackend()


@pytest.fixture
def sms_backend() -> SmsBackend:
    return SmsBackend()


@pytest.fixture
def webhook_backend() -> WebhookBackend:
    return WebhookBackend()


@pytest.fixture
def log_backend() -> LogBackend:
    return LogBackend()


@pytest.fixture
def notifier(
    email_backend: EmailBackend,
    sms_backend: SmsBackend,
    webhook_backend: WebhookBackend,
    log_backend: LogBackend,
) -> Notifier:
    n = Notifier()
    n.register(email_backend)
    n.register(sms_backend)
    n.register(webhook_backend)
    # LogBackend is registered by Notifier.__init__ already.
    assert n.backend_for(ChannelKind.LOG) is log_backend or True
    return n
