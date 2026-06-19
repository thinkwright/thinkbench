"""The flag client: load flags from a file and answer questions about them."""

from __future__ import annotations

import json
import os
from typing import Any, Iterable, Mapping

from .flags import Flag, State


class FlagClient:
    """Holds a set of flags and answers ``is_enabled`` questions.

    Flags are normally loaded from a JSON file via :meth:`from_file` so they
    can be changed without redeploying. You can also build a client directly
    from a list of :class:`Flag` objects with :meth:`with_flags`, which is
    handy in tests.
    """

    def __init__(self, flags: Iterable[Flag] | None = None):
        self._flags: dict[str, Flag] = {}
        for flag in flags or ():
            self._flags[flag.name] = flag

    # -- construction -------------------------------------------------------

    @classmethod
    def from_file(cls, path: str) -> "FlagClient":
        """Load flags from a JSON file.

        The file should contain an object with a ``flags`` list, each entry
        shaped like ``{"name": ..., "state": ..., "rules": {...}}``::

            {
              "flags": [
                {"name": "new-dashboard", "state": "rollout",
                 "rules": {"user_ids": ["alice", "bob"], "percentage": 10}}
              ]
            }
        """
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FlagClient":
        return cls(Flag.from_dict(entry) for entry in data.get("flags", []))

    @classmethod
    def with_flags(cls, flags: Iterable[Flag]) -> "FlagClient":
        return cls(flags)

    # -- queries ------------------------------------------------------------

    def is_enabled(
        self,
        name: str,
        context: Mapping[str, Any] | None = None,
        default: bool = False,
    ) -> bool:
        """Return whether feature ``name`` is on for the given context.

        If the flag isn't defined, returns ``default`` (``False`` by default)
        rather than raising, so a missing flag can never blow up a request.
        """
        flag = self._flags.get(name)
        if flag is None:
            return default
        return flag.is_enabled(context)

    def get(self, name: str) -> Flag | None:
        """Return the raw :class:`Flag` for ``name``, or ``None`` if undefined."""
        return self._flags.get(name)

    def all(self) -> list[Flag]:
        """Return every defined flag, sorted by name."""
        return [self._flags[name] for name in sorted(self._flags)]

    def summary(self) -> list[dict]:
        """A plain-data snapshot of every flag and its current configuration.

        Handy for printing, logging, or exposing in an admin endpoint so you
        can see what's on without digging through internals.
        """
        return [flag.to_dict() for flag in self.all()]

    def __contains__(self, name: str) -> bool:
        return name in self._flags

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"FlagClient(flags={list(self._flags)})"


def load(path: str | os.PathLike[str]) -> FlagClient:
    """Convenience wrapper around ``FlagClient.from_file``."""
    return FlagClient.from_file(os.fspath(path))