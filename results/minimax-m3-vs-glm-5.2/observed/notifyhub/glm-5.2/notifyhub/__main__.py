"""Fire a notification from the command line.

Usage::

    python -m notifyhub --to-log --subject "Hi" --body "from the shell"
    python -m notifyhub --email alice@example.com --subject "Hi" \\
        --body "real body"

Every ``--email``/``--phone``/``--webhook``/``--to-log`` flag both
registers the matching channel (where one is needed) and gives the
recipient an address for it, so the same call reaches however you
configured it.  Exit code is non-zero if any attempted send failed.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import List

from . import (
    Hub,
    LogChannel,
    EmailChannel,
    SMSChannel,
    WebhookChannel,
    Recipient,
    Message,
)


def _build(argv: List[str] | None) -> tuple[Hub, Recipient, Message, bool]:
    p = argparse.ArgumentParser(
        prog="notifyhub",
        description="Send a notification to a person via notifyhub.",
    )
    p.add_argument("--id", default="cli", help="recipient id (default: cli)")
    p.add_argument("--subject", required=True, help="message subject")
    p.add_argument("--body", default="", help="message body")
    p.add_argument("--to-log", action="store_true", help="deliver via the log channel")
    p.add_argument("--email", metavar="ADDR", help="deliver via email to ADDR")
    p.add_argument("--phone", metavar="NUMBER", help="deliver via SMS to NUMBER")
    p.add_argument("--webhook", metavar="URL", help="deliver via webhook to URL")
    p.add_argument("--strict", action="store_true", help="raise on first failure")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    hub = Hub(strict=args.strict)
    addresses: dict[str, str] = {}

    if args.to_log:
        hub.use(LogChannel())
        # LogChannel doesn't need an address, but the hub skips channels
        # the recipient has no address for, so give it a placeholder.
        addresses["log"] = "stdout"
    if args.email:
        hub.use(EmailChannel())
        addresses["email"] = args.email
    if args.phone:
        hub.use(SMSChannel())
        addresses["phone"] = args.phone
    if args.webhook:
        hub.use(WebhookChannel())
        addresses["webhook"] = args.webhook

    recipient = Recipient(args.id, **addresses)
    message = Message(args.subject, args.body)
    return hub, recipient, message, bool(args.to_log or args.email or args.phone or args.webhook)


def main(argv: List[str] | None = None) -> int:
    hub, recipient, message, configured = _build(argv)
    if not configured:
        print("error: specify at least one delivery channel", file=sys.stderr)
        return 2

    report = hub.notify(recipient, message)
    for r in report:
        print(f"  {r.channel}: {r.status}", file=sys.stderr)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())