"""Recipient and Channel construction and ordering."""

import pytest

from notifyhub.recipient import Channel, ChannelKind, Recipient


def test_channel_requires_kind_and_address():
    with pytest.raises(TypeError):
        Channel(kind="email", address="x@example.com")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Channel(kind=ChannelKind.EMAIL, address="")
    with pytest.raises(ValueError):
        Channel(kind=ChannelKind.EMAIL, address="   ")


def test_recipient_requires_at_least_one_channel():
    with pytest.raises(ValueError):
        Recipient(name="alice", channels=())


def test_recipient_requires_name():
    with pytest.raises(ValueError):
        Recipient(name="", channels=(Channel(ChannelKind.EMAIL, "x@y"),))


def test_recipient_of_factory():
    r = Recipient.of(
        "alice",
        Channel(ChannelKind.EMAIL, "a@e.com", priority=20),
        Channel(ChannelKind.SMS, "+1", priority=10),
    )
    assert r.name == "alice"
    assert len(r.channels) == 2


def test_ordered_sorts_by_priority_low_first():
    r = Recipient(
        name="a",
        channels=(
            Channel(ChannelKind.SMS, "+1", priority=20),
            Channel(ChannelKind.EMAIL, "a@e.com", priority=10),
            Channel(ChannelKind.WEBHOOK, "https://x", priority=15),
        ),
    )
    kinds = [c.kind for c in r.ordered()]
    assert kinds == [ChannelKind.EMAIL, ChannelKind.WEBHOOK, ChannelKind.SMS]


def test_ordered_is_stable_on_ties():
    r = Recipient(
        name="a",
        channels=(
            Channel(ChannelKind.EMAIL, "a@e.com", priority=10),
            Channel(ChannelKind.SMS, "+1", priority=10),
        ),
    )
    # Same priority — order should follow insertion (kind name as tiebreaker).
    kinds = [c.kind for c in r.ordered()]
    assert kinds == [ChannelKind.EMAIL, ChannelKind.SMS]
