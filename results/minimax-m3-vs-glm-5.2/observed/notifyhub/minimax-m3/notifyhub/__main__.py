"""CLI: `python -m notifyhub "hello" --to user@example.com`.

Useful for smoke-testing and for shell scripts that want to fire a notification
without writing Python. The recipient is built from the flags you pass; if you
only give an email, that's the only channel. If you give multiple --to flags,
each becomes a channel in the order given (priority = order).
"""

from __future__ import annotations

import argparse
import sys

from notifyhub.message import Message
from notifyhub.notifier import Notifier
from notifyhub.recipient import Channel, ChannelKind, Recipient


_KIND_FROM_PREFIX = {
    "mailto:": ChannelKind.EMAIL,
    "sms:": ChannelKind.SMS,
    "http://": ChannelKind.WEBHOOK,
    "https://": ChannelKind.WEBHOOK,
    "log:": ChannelKind.LOG,
}


def _channel_from_arg(raw: str, priority: int) -> Channel:
    for prefix, kind in _KIND_FROM_PREFIX.items():
        if raw.startswith(prefix):
            return Channel(kind=kind, address=raw[len(prefix):], priority=priority)
    # No recognized prefix — assume email, which is the most common case.
    return Channel(kind=ChannelKind.EMAIL, address=raw, priority=priority)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="notifyhub",
        description="Send a notification to a recipient.",
    )
    parser.add_argument("body", help="message body")
    parser.add_argument(
        "--subject", "-s", default="", help="message subject (optional)",
    )
    parser.add_argument(
        "--to", "-t", action="append", default=[],
        help="recipient address; repeat for multiple channels. "
             "Prefix with mailto:/sms:/http(s)://:/log: to pick a kind. "
             "Defaults to email.",
    )
    parser.add_argument(
        "--name", "-n", default=None,
        help="recipient name (defaults to the first --to address)",
    )
    args = parser.parse_args(argv)

    if not args.to:
        print("error: at least one --to is required", file=sys.stderr)
        return 2

    channels = tuple(
        _channel_from_arg(addr, priority=i) for i, addr in enumerate(args.to)
    )
    recipient = Recipient(name=args.name or args.to[0], channels=channels)
    message = Message(subject=args.subject, body=args.body)

    try:
        channel = Notifier().send(recipient, message)
    except Exception as err:
        print(f"notifyhub: {err}", file=sys.stderr)
        return 1
    print(f"delivered via {channel.kind.value} ({channel.address})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
