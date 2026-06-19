"""Flag data model and evaluation rules."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping


class State:
    """The possible states of a flag."""

    OFF = "off"
    ON = "on"
    ROLLOUT = "rollout"


@dataclass(frozen=True)
class Flag:
    """A single feature flag.

    ``state`` is one of ``State.OFF``, ``State.ON`` or ``State.ROLLOUT``.
    When the state is ``ROLLOUT``, ``rules`` describes who the flag is on for:

    * ``percentage`` (0-100): turned on for that fraction of users, chosen
      deterministically by hashing ``user_id`` so the same user always gets
      the same answer.
    * ``user_ids``: a list of user ids the flag is explicitly on for.
    * ``environments``: a list of environment names (e.g. ``"staging"``)
      where the flag is on. The environment comes from the evaluation
      context under the key ``env``.

    Rules combine with AND: a user must match *every* rule that's present.
    Leave a rule out to ignore it.
    """

    name: str
    state: str = State.OFF
    rules: Mapping[str, Any] = field(default_factory=dict)

    def is_enabled(self, context: Mapping[str, Any] | None = None) -> bool:
        """Evaluate this flag for the given context.

        ``context`` is a mapping that may carry ``user_id`` and ``env`` among
        other things. It may be ``None`` or empty.
        """
        if self.state == State.ON:
            return True
        if self.state == State.OFF:
            return False
        if self.state == State.ROLLOUT:
            return self._evaluate_rollout(context or {})
        # Unknown state: fail closed.
        return False

    def _evaluate_rollout(self, context: Mapping[str, Any]) -> bool:
        rules = self.rules or {}
        if not rules:
            return False

        if "user_ids" in rules:
            user_id = context.get("user_id")
            if user_id is None or user_id not in set(rules["user_ids"]):
                return False

        if "environments" in rules:
            env = context.get("env")
            if env is None or env not in set(rules["environments"]):
                return False

        if "percentage" in rules:
            user_id = context.get("user_id")
            if user_id is None:
                # No user to bucket on: fail closed.
                return False
            if not self._user_in_percentage(user_id, rules["percentage"]):
                return False

        return True

    @staticmethod
    def _user_in_percentage(user_id: str, percentage: int) -> bool:
        """Deterministically decide whether ``user_id`` falls in ``percentage``.

        Uses a stable hash of the user id so the same user always lands on the
        same side of the line, even across processes and restarts.
        """
        if percentage <= 0:
            return False
        if percentage >= 100:
            return True
        digest = hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()
        # First 8 hex chars -> 32-bit int -> 0..99 bucket.
        bucket = int(digest[:8], 16) % 100
        return bucket < percentage

    def to_dict(self) -> dict:
        return {"name": self.name, "state": self.state, "rules": dict(self.rules)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Flag":
        return cls(
            name=data["name"],
            state=data.get("state", State.OFF),
            rules=data.get("rules", {}) or {},
        )

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return json.dumps(self.to_dict())