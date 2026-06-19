"""The Notifier: routing, fallback, and error reporting."""

import pytest

from notifyhub.errors import DeliveryError, NoReachableChannel
from notifyhub.message import Message
from notifyhub.notifier import Notifier
from notifyhub.recipient import Channel, ChannelKind, Recipient


def test_send_picks_highest_priority_channel(notifier, alice, message, email_backend):
    used = notifier.send(alice, message)
    assert used.kind == ChannelKind.EMAIL
    assert len(email_backend.sent) == 1


def test_send_falls_back_when_first_channel_fails(
    notifier, alice, message, email_backend, sms_backend,
):
    email_backend.fail_with = RuntimeError("smtp down")
    used = notifier.send(alice, message)
    assert used.kind == ChannelKind.SMS
    assert email_backend.sent == []
    assert len(sms_backend.sent) == 1


def test_send_raises_when_every_channel_fails(
    notifier, alice, message, email_backend, sms_backend,
):
    email_backend.fail_with = RuntimeError("smtp down")
    sms_backend.fail_with = RuntimeError("carrier down")
    with pytest.raises(NoReachableChannel) as ei:
        notifier.send(alice, message)
    assert ei.value.recipient is alice
    assert len(ei.value.attempts) == 2
    assert all(isinstance(a, DeliveryError) for a in ei.value.attempts)


def test_send_skips_channels_with_no_backend(message):
    # Only an SMS channel, but no SMS backend registered.
    n = Notifier()  # only LOG backend by default
    r = Recipient(
        name="carol",
        channels=(Channel(ChannelKind.SMS, "+1", priority=10),),
    )
    with pytest.raises(NoReachableChannel) as ei:
        n.send(r, message)
    # SMS had no backend, so it was skipped (not counted as an attempt).
    assert ei.value.attempts == []


def test_send_uses_log_backend_when_only_log_channel(message, bob_log_only):
    n = Notifier()
    used = n.send(bob_log_only, message)
    assert used.kind == ChannelKind.LOG


def test_unknown_kind_raises_runtime_error_on_lookup():
    n = Notifier()
    with pytest.raises(RuntimeError):
        n.backend_for(ChannelKind.SMS)


def test_register_overrides_default_log_backend():
    n = Notifier()
    custom = LogBackend(logger_name="custom")
    n.register(custom)
    assert n.backend_for(ChannelKind.LOG) is custom
