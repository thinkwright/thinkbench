"""Backends: what they record and how they fail."""

import logging

import pytest

from notifyhub.backends import EmailBackend, LogBackend, SmsBackend, WebhookBackend
from notifyhub.errors import DeliveryError
from notifyhub.message import Message
from notifyhub.recipient import Channel, ChannelKind, Recipient


def test_email_backend_records_send(alice, message):
    be = EmailBackend()
    channel = next(c for c in alice.channels if c.kind == ChannelKind.EMAIL)
    be.deliver(alice, channel, message)
    assert len(be.sent) == 1
    record = be.sent[0]
    assert record["to"] == "alice@example.com"
    assert record["subject"] == "hi"
    assert record["body"] == "hello there"
    assert record["metadata"] == {"trace": "abc"}


def test_sms_backend_collapses_subject_and_body(alice, message):
    be = SmsBackend()
    channel = next(c for c in alice.channels if c.kind == ChannelKind.SMS)
    be.deliver(alice, channel, message)
    assert be.sent[0]["text"] == "hello there"


def test_sms_backend_uses_subject_when_body_empty(alice):
    be = SmsBackend()
    channel = next(c for c in alice.channels if c.kind == ChannelKind.SMS)
    be.deliver(alice, channel, Message(subject="just a subject"))
    assert be.sent[0]["text"] == "just a subject"


def test_webhook_backend_records_payload(alice, message):
    be = WebhookBackend()
    channel = Channel(ChannelKind.WEBHOOK, "https://hooks.example.com/x")
    be.deliver(alice, channel, message)
    assert be.sent[0]["url"] == "https://hooks.example.com/x"
    assert be.sent[0]["payload"]["subject"] == "hi"
    assert be.sent[0]["payload"]["recipient"]["address"] == channel.address


def test_backend_fail_with_raises_delivery_error(alice, message):
    be = EmailBackend(fail_with=RuntimeError("smtp down"))
    channel = next(c for c in alice.channels if c.kind == ChannelKind.EMAIL)
    with pytest.raises(DeliveryError) as ei:
        be.deliver(alice, channel, message)
    assert ei.value.channel is channel
    assert ei.value.recipient is alice
    assert isinstance(ei.value.cause, RuntimeError)
    assert "smtp down" in str(ei.value)


def test_log_backend_writes_to_logger(alice, message, caplog):
    be = LogBackend()
    channel = Channel(ChannelKind.LOG, "notifyhub.test_log")
    with caplog.at_level(logging.INFO, logger="notifyhub.test_log"):
        be.deliver(alice, channel, message)
    assert any("alice" in r.getMessage() for r in caplog.records)
    assert any("hello there" in r.getMessage() for r in caplog.records)
