"""The thing we want to deliver."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class Message:
    """A message addressed to a person.

    `subject` is a short headline (used by email-like channels; ignored by SMS/log).
    `body` is the actual content. `metadata` is free-form context callers can attach
    (priority tags, template ids, trace ids) — backends may use it or ignore it.
    """

    subject: str = ""
    body: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.subject, str):
            raise TypeError("subject must be a str")
        if not isinstance(self.body, str):
            raise TypeError("body must be a str")
        if not self.subject and not self.body:
            raise ValueError("a Message needs at least a subject or a body")
