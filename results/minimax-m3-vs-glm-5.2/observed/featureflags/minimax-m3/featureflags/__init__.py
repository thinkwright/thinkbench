"""featureflags — small library for toggling functionality at runtime.

Public API:
    is_enabled(name, **context) -> bool
    registry() -> Registry
    reload() -> None

Flags are loaded from a JSON file. The path is taken from the
``FEATUREFLAGS_PATH`` environment variable, falling back to
``./featureflags.json``.

A flag definition looks like::

    {
        "new_checkout": {
            "enabled": true,
            "environments": ["prod", "staging"],
            "rollout": {"percent": 25, "key": "user_id"}
        }
    }

- ``enabled`` (bool, default ``False``): master switch.
- ``environments`` (list[str], optional): if set, the flag is only on when
  the current environment (from ``FEATUREFLAGS_ENV`` or ``context['env']``)
  is in the list.
- ``rollout`` (dict, optional): percentage rollout. ``percent`` is 0–100.
  ``key`` (default ``"user_id"``) names the context attribute used to make
  the decision stable per subject.

Unknown flags return ``False`` rather than raising.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Mapping

__all__ = ["is_enabled", "registry", "reload", "Registry", "Flag"]

DEFAULT_PATH = "featureflags.json"
ENV_PATH = "FEATUREFLAGS_PATH"
ENV_NAME = "FEATUREFLAGS_ENV"


def _load_path() -> Path:
    return Path(os.environ.get(ENV_PATH, DEFAULT_PATH))


def _current_env(context: Mapping[str, Any]) -> str | None:
    if "env" in context and context["env"] is not None:
        return str(context["env"])
    return os.environ.get(ENV_NAME) or None


class Flag:
    """A single flag definition. Evaluation logic lives here."""

    __slots__ = ("name", "enabled", "environments", "rollout")

    def __init__(
        self,
        name: str,
        enabled: bool = False,
        environments: list[str] | None = None,
        rollout: Mapping[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.enabled = bool(enabled)
        self.environments = list(environments) if environments else None
        self.rollout = dict(rollout) if rollout else None

    def evaluate(self, context: Mapping[str, Any]) -> bool:
        if not self.enabled:
            return False
        if self.environments is not None:
            env = _current_env(context)
            if env is None or env not in self.environments:
                return False
        if self.rollout is not None:
            return self._rollout_in(context)
        return True

    def _rollout_in(self, context: Mapping[str, Any]) -> bool:
        try:
            percent = float(self.rollout.get("percent", 0))
        except (TypeError, ValueError):
            return False
        if percent <= 0:
            return False
        if percent >= 100:
            return True
        key_name = self.rollout.get("key", "user_id")
        subject = context.get(key_name)
        if subject is None:
            # No stable subject available — be conservative: off.
            return False
        bucket = _stable_bucket(self.name, str(subject))
        return bucket < percent

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"enabled": self.enabled}
        if self.environments is not None:
            out["environments"] = list(self.environments)
        if self.rollout is not None:
            out["rollout"] = dict(self.rollout)
        return out


def _stable_bucket(flag_name: str, subject: str) -> float:
    """Map (flag, subject) to a stable float in [0, 100).

    Uses the first 8 bytes of SHA-256 as an unsigned int, divided by 2**64
    and scaled to 100. Stable across processes and platforms.
    """
    digest = hashlib.sha256(f"{flag_name}\x00{subject}".encode("utf-8")).digest()
    n = int.from_bytes(digest[:8], "big", signed=False)
    return (n / 2**64) * 100.0


class Registry:
    """Holds the loaded flags. Thread-safe; reload swaps in a new snapshot."""

    def __init__(self, flags: Mapping[str, Flag] | None = None) -> None:
        self._flags: dict[str, Flag] = dict(flags) if flags else {}
        self._lock = threading.Lock()

    def is_enabled(self, name: str, **context: Any) -> bool:
        with self._lock:
            flag = self._flags.get(name)
        if flag is None:
            return False
        return flag.evaluate(context)

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._flags.keys())

    def get(self, name: str) -> Flag | None:
        with self._lock:
            return self._flags.get(name)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Return a JSON-serialisable view of every flag — for inspection."""
        with self._lock:
            return {name: flag.as_dict() for name, flag in sorted(self._flags.items())}

    def replace(self, flags: Mapping[str, Flag]) -> None:
        with self._lock:
            self._flags = dict(flags)


def _parse(raw: Mapping[str, Any]) -> dict[str, Flag]:
    out: dict[str, Flag] = {}
    for name, spec in raw.items():
        if not isinstance(spec, Mapping):
            # Skip malformed entries silently — bad config shouldn't crash
            # the app; the flag just won't exist.
            continue
        out[name] = Flag(
            name=name,
            enabled=spec.get("enabled", False),
            environments=spec.get("environments"),
            rollout=spec.get("rollout"),
        )
    return out


def _load_from_disk() -> dict[str, Flag]:
    path = _load_path()
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        # Corrupt or unreadable config: behave as if no flags are defined.
        return {}
    if not isinstance(raw, Mapping):
        return {}
    return _parse(raw)


# Module-level registry, lazily initialised.
_registry: Registry | None = None
_registry_lock = threading.Lock()


def _get_registry() -> Registry:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = Registry(_load_from_disk())
    return _registry


def is_enabled(name: str, **context: Any) -> bool:
    """Return True iff the named flag is on for the given context.

    Unknown flags return False. Never raises.
    """
    return _get_registry().is_enabled(name, **context)


def registry() -> Registry:
    """Return the live registry. Use ``.snapshot()`` to inspect flags."""
    return _get_registry()


def reload() -> None:
    """Re-read flags from disk into the live registry."""
    _get_registry().replace(_load_from_disk())
