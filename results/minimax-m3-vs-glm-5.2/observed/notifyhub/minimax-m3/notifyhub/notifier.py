"""The dispatcher: pick a channel, hand the message to the right backend."""

from __future__ import annotations

import logging
from typing import Mapping, Optional

from notifyhub.backends import Backend, LogBackend
from notifyhub.errors import DeliveryError, NoReachableChannel
from notifyhub.message import Message
from notifyhub.recipient import Channel, ChannelKind, Recipient

_log = logging.getLogger("notifyhub")


class Notifier:
    """Routes messages to backends.

    Backends are looked up by `ChannelKind`. Unknown kinds raise — that's a
    programming error, not a delivery failure, so it surfaces immediately
    instead of being swallowed.
    """

    def __init__(self, backends: Optional[Mapping[ChannelKind, Backend]] = None) -> None:
        self._backends: dict[ChannelKind, Backend] = dict(backends or {})
        # Always have a log backend available; it's the dev-time fallback.
        self._backends.setdefault(ChannelKind.LOG, LogBackend())

    def register(self, backend: Backend) -> None:
        self._backends[backend.kind] = backend

    def backend_for(self, kind: ChannelKind) -> Backend:
        try:
            return self._backends[kind]
        except KeyError as e:
            raise RuntimeError(
                f"no backend registered for channel kind {kind.value!r}"
            ) from e

    def send(self, recipient: Recipient, message: Message) -> Channel:
        """Deliver `message` to `recipient`. Returns the channel that succeeded.

        Tries channels in priority order. On a `DeliveryError`, logs it and
        moves to the next channel. If every channel fails, raises
        `NoReachableChannel` carrying every attempt.
        """
        attempts: list[DeliveryError] = []
        for channel in recipient.ordered():
            backend = self._backends.get(channel.kind)
            if backend is None:
                _log.warning(
                    "no backend for %s; skipping channel %s for %s",
                    channel.kind.value, channel.address, recipient.name,
                )
                continue
            try:
                backend.deliver(recipient, channel, message)
            except DeliveryError as err:
                _log.warning("delivery attempt failed: %s", err)
                attempts.append(err)
                continue
            _log.info(
                "delivered to %s via %s (%s)",
                recipient.name, channel.kind.value, channel.address,
            )
            return channel

        raise NoReachableChannel(recipient, attempts)


def send(recipient: Recipient, message: Message) -> Channel:
    """Module-level convenience: use a default Notifier.

    The default Notifier has only the LogBackend registered, so this works
    out of the box for development. Real callers should construct a Notifier
    with their own backends and call `.send()` on it.
    """
    return _default_notifier.send(recipient, message)


_default_notifier = Notifier()
