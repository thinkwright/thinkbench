"""Webhook channel.

POSTs the message as JSON to a recipient-specific URL using only the
standard library (``urllib``).  The address key is ``webhook`` and should
be a full URL.

A send is considered successful if the endpoint returns a 2xx status;
anything else (network error, non-2xx) raises and becomes a failed
:class:`SendResult`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Mapping, Optional

from .base import Channel
from ..recipient import Recipient
from ..message import Message


class WebhookChannel(Channel):
    name = "webhook"

    def __init__(self, timeout: float = 10.0, headers: Optional[Mapping[str, str]] = None) -> None:
        self.timeout = timeout
        self._headers = dict(headers) if headers else {}

    def _payload(self, recipient: Recipient, message: Message) -> bytes:
        body: dict[str, Any] = {
            "recipient": recipient.id,
            "subject": message.subject,
            "body": message.body,
        }
        if message.data:
            body["data"] = message.data
        return json.dumps(body).encode("utf-8")

    def send(self, recipient: Recipient, message: Message) -> None:
        url = recipient.address_for(self.name)
        if not url:
            raise ValueError(f"recipient {recipient.id!r} has no webhook URL")

        headers = {"Content-Type": "application/json"}
        headers.update(self._headers)
        req = urllib.request.Request(url, data=self._payload(recipient, message), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = getattr(resp, "status", resp.getcode())
                if not (200 <= status < 300):
                    raise urllib.error.HTTPError(
                        url, status, f"webhook returned {status}", resp.headers, None
                    )
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError as exc:
            raise IOError(f"webhook unreachable: {exc.reason}") from exc