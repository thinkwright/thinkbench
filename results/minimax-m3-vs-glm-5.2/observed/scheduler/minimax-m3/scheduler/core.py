"""Core scheduling primitives.

A `Task` pairs a callable with a `Schedule`. A `Scheduler` owns a set of tasks
and ticks once per second on a background thread, firing any task whose
schedule says it's due.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from typing import Callable, Iterable

log = logging.getLogger("scheduler")


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------


class Schedule:
    """Base class for schedules. Subclasses implement `next_after`."""

    def next_after(self, now: datetime) -> datetime:
        raise NotImplementedError


@dataclass(frozen=True)
class Interval(Schedule):
    """Fire every `seconds` seconds, aligned to the first run."""

    seconds: float

    def __post_init__(self) -> None:
        if self.seconds <= 0:
            raise ValueError("Interval.seconds must be positive")

    def next_after(self, now: datetime) -> datetime:
        return now + timedelta(seconds=self.seconds)


@dataclass(frozen=True)
class DailyAt(Schedule):
    """Fire once per day at the given wall-clock time."""

    at: dtime

    def next_after(self, now: datetime) -> datetime:
        candidate = now.replace(
            hour=self.at.hour,
            minute=self.at.minute,
            second=self.at.second,
            microsecond=0,
        )
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------


@dataclass
class Task:
    """A named callable paired with a schedule."""

    name: str
    func: Callable[[], None]
    schedule: Schedule
    # Bookkeeping — not part of the user's mental model, but useful for tests
    # and for the "did it run?" question the user cares about.
    last_run: datetime | None = field(default=None, init=False, repr=False)
    run_count: int = field(default=0, init=False, repr=False)


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class Scheduler:
    """Runs registered tasks on their schedules.

    Usage:
        s = Scheduler()
        s.register(Task("ping", ping_fn, Interval(seconds=30)))
        s.start()
        ...
        s.stop()
    """

    def __init__(self, tasks: Iterable[Task] | None = None) -> None:
        self._tasks: list[Task] = []
        self._next_due: dict[str, datetime] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if tasks:
            for t in tasks:
                self.register(t)

    # -- registration -------------------------------------------------------

    def register(self, task: Task) -> None:
        with self._lock:
            if any(t.name == task.name for t in self._tasks):
                raise ValueError(f"task {task.name!r} already registered")
            self._tasks.append(task)
            self._next_due[task.name] = task.schedule.next_after(datetime.now())

    def tasks(self) -> list[Task]:
        with self._lock:
            return list(self._tasks)

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Start the background tick thread. Idempotent."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="scheduler-tick", daemon=True
        )
        self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        """Signal the tick thread to stop and wait for it."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    def run_forever(self) -> None:
        """Block until `stop()` is called. Useful from a CLI or main script."""
        self.start()
        try:
            while not self._stop.wait(timeout=0.5):
                pass
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    # -- ticking ------------------------------------------------------------

    def _run(self) -> None:
        # Tick once a second. One second is the smallest unit we care about
        # for the kinds of chores this is meant for (reports, cache refreshes,
        # cleanups). Going finer would burn CPU for no real benefit.
        while not self._stop.wait(timeout=1.0):
            try:
                self._tick()
            except Exception:  # never let a tick die
                log.exception("tick failed")

    def _tick(self) -> None:
        now = datetime.now()
        # Snapshot under the lock so we don't hold it while running user code.
        with self._lock:
            due = [
                (t, self._next_due[t.name])
                for t in self._tasks
                if self._next_due[t.name] <= now
            ]
            for t, _ in due:
                # Reschedule immediately so a slow task doesn't get re-fired
                # in the same tick.
                self._next_due[t.name] = t.schedule.next_after(now)

        for t, _due_at in due:
            self._fire(t, now)

    def _fire(self, task: Task, now: datetime) -> None:
        try:
            task.func()
        except Exception:
            log.exception("task %s raised", task.name)
        finally:
            task.last_run = now
            task.run_count += 1

    # -- introspection ------------------------------------------------------

    def next_run(self, name: str) -> datetime | None:
        with self._lock:
            return self._next_due.get(name)
