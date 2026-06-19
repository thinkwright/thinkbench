"""Schedules: descriptions of when a task should run.

A Schedule is a callable that answers one question: given the time a task last
fired (or None if it never has), when should it fire next? Returning None means
"never again". This single method is enough for the Scheduler to drive both
fixed-interval and time-of-day recurring tasks.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

__all__ = ["Schedule", "every", "at"]


class Schedule:
    """Base class. Subclasses implement ``next_run(after) -> datetime | None``.

    ``after`` is the previous fire time (aware datetime) or None for the first
    run. The returned datetime must be timezone-aware and strictly later than
    ``after`` (when ``after`` is not None).
    """

    def next_run(self, after: _dt.datetime | None) -> _dt.datetime | None:
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"{type(self).__name__}()"


@dataclass(frozen=True)
class Interval(Schedule):
    """Fire every fixed interval: ``every(minutes=5)``.

    The first run is scheduled one interval from "now" (whenever the scheduler
    asks), and each subsequent run one interval after the previous.
    """

    seconds: int = 0
    minutes: int = 0
    hours: int = 0
    days: int = 0
    weeks: int = 0

    def __post_init__(self) -> None:
        total = (
            self.seconds
            + self.minutes * 60
            + self.hours * 3600
            + self.days * 86400
            + self.weeks * 604800
        )
        if total <= 0:
            raise ValueError("interval must be positive")
        object.__setattr__(self, "_total_seconds", total)

    @property
    def total_seconds(self) -> int:
        return self._total_seconds  # type: ignore[attr-defined]

    def next_run(self, after: _dt.datetime | None) -> _dt.datetime | None:
        base = after if after is not None else _dt.datetime.now(_dt.timezone.utc)
        return base + _dt.timedelta(seconds=self.total_seconds)

    def __repr__(self) -> str:
        parts = []
        for name, value in [
            ("weeks", self.weeks),
            ("days", self.days),
            ("hours", self.hours),
            ("minutes", self.minutes),
            ("seconds", self.seconds),
        ]:
            if value:
                parts.append(f"{name}={value}")
        return f"every({', '.join(parts)})"


def every(
    *, seconds: int = 0, minutes: int = 0, hours: int = 0, days: int = 0, weeks: int = 0
) -> Interval:
    """Build a fixed-interval schedule. At least one unit must be positive."""
    return Interval(
        seconds=seconds, minutes=minutes, hours=hours, days=days, weeks=weeks
    )


@dataclass(frozen=True)
class Daily(Schedule):
    """Fire every day at a given local time-of-day.

    ``time_of_day`` is an aware ``time`` (carries its own tzinfo) or a naive
    ``time`` interpreted in ``tz`` (which defaults to UTC). The next run is the
    soonest occurrence of that time strictly after ``after``.
    """

    time_of_day: _dt.time
    tz: _dt.tzinfo = _dt.timezone.utc

    def __post_init__(self) -> None:
        if self.time_of_day.tzinfo is None:
            aware = self.time_of_day.replace(tzinfo=self.tz)
            object.__setattr__(self, "time_of_day", aware)

    def next_run(self, after: _dt.datetime | None) -> _dt.datetime | None:
        base = after if after is not None else _dt.datetime.now(_dt.timezone.utc)
        # Work in the schedule's own timezone so the wall-clock time is stable.
        local_base = base.astimezone(self.time_of_day.tzinfo)
        candidate = _dt.datetime.combine(local_base.date(), self.time_of_day)
        if candidate <= base:
            candidate += _dt.timedelta(days=1)
        return candidate.astimezone(_dt.timezone.utc)

    def __repr__(self) -> str:
        return f"at({self.time_of_day.strftime('%H:%M')})"


def at(time_str: str, *, tz: _dt.tzinfo = _dt.timezone.utc) -> Daily:
    """Build a daily schedule at ``"HH:MM"`` (24h) in ``tz`` (default UTC).

    Example: ``at("02:30")`` fires daily at 02:30 UTC.
    """
    hh, _, mm = time_str.partition(":")
    try:
        hour, minute = int(hh), int(mm)
    except ValueError as e:
        raise ValueError(f"time must be 'HH:MM', got {time_str!r}") from e
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"time out of range: {time_str!r}")
    return Daily(time_of_day=_dt.time(hour, minute), tz=tz)