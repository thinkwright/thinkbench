"""The Scheduler: registers tasks and fires them when they're due."""

from __future__ import annotations

import datetime as _dt
import heapq
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .schedules import Schedule

__all__ = ["Scheduler", "Task"]

log = logging.getLogger("scheduler")

_UTC = _dt.timezone.utc


@dataclass(order=True)
class _Pending:
    run_at: _dt.datetime
    seq: int = field(compare=True)
    task: "Task" = field(compare=False)


@dataclass
class Task:
    """A registered unit of work.

    ``name`` defaults to the callable's ``__name__``. ``last_run`` is the last
    time the task fired (None until it has). ``runs`` counts firings.
    """

    schedule: Schedule
    func: Callable[[], None]
    name: str = ""
    last_run: Optional[_dt.datetime] = None
    runs: int = 0

    def __post_init__(self) -> None:
        if not self.name:
            self.name = getattr(self.func, "__name__", "task")


class Scheduler:
    """Holds tasks and fires them on schedule.

    Usage::

        sched = Scheduler()
        sched.add(every(minutes=5), refresh)
        sched.add(at("02:30"), report)
        sched.run()          # blocks, firing tasks forever
        # or: sched.run_once() to fire whatever is overdue right now

    All times are kept in UTC internally; schedules express their own intent
    (intervals are tz-agnostic, ``at()`` honors its timezone).
    """

    def __init__(self) -> None:
        self._tasks: list[Task] = []
        self._heap: list[_Pending] = []
        self._seq = 0
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # -- registration -------------------------------------------------------

    def add(self, schedule: Schedule, func: Callable[[], None], *, name: str = "") -> Task:
        """Register ``func`` to run on ``schedule``. Returns the Task."""
        task = Task(schedule=schedule, func=func, name=name)
        self._tasks.append(task)
        self._schedule_next(task, after=None)
        return task

    def remove(self, task: Task) -> None:
        """Remove a task so it won't fire again."""
        with self._lock:
            if task in self._tasks:
                self._tasks.remove(task)
            self._heap = [p for p in self._heap if p.task is not task]
            heapq.heapify(self._heap)

    @property
    def tasks(self) -> list[Task]:
        """A snapshot of registered tasks."""
        return list(self._tasks)

    # -- internals ----------------------------------------------------------

    def _schedule_next(self, task: Task, after: Optional[_dt.datetime]) -> None:
        nxt = task.schedule.next_run(after)
        if nxt is None:
            return
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=_UTC)
        with self._lock:
            self._seq += 1
            heapq.heappush(self._heap, _Pending(run_at=nxt, seq=self._seq, task=task))

    def _pop_due(self, now: _dt.datetime) -> Optional[_Pending]:
        with self._lock:
            if self._heap and self._heap[0].run_at <= now:
                return heapq.heappop(self._heap)
        return None

    def _fire(self, pending: _Pending) -> None:
        task = pending.task
        try:
            task.func()
        except Exception:
            log.exception("task %r raised", task.name)
        finally:
            task.last_run = pending.run_at
            task.runs += 1
            self._schedule_next(task, after=pending.run_at)

    # -- running ------------------------------------------------------------

    def run_once(self, now: Optional[_dt.datetime] = None) -> int:
        """Fire every task whose scheduled time has passed. Returns the count.

        Does not block. Useful in tests or for a host that wants to drive the
        scheduler itself.
        """
        now = now or _dt.now(_UTC)
        fired = 0
        while (pending := self._pop_due(now)) is not None:
            self._fire(pending)
            fired += 1
        return fired

    def run(self, *, max_iterations: Optional[int] = None) -> None:
        """Block, firing tasks as they come due.

        ``max_iterations`` (if given) stops after that many firings, handy for
        tests. Otherwise runs until :meth:`stop` is called (e.g. from another
        thread or a signal handler).
        """
        iterations = 0
        while not self._stop.is_set():
            now = _dt.now(_UTC)
            pending = self._pop_due(now)
            if pending is None:
                with self._lock:
                    next_at = self._heap[0].run_at if self._heap else None
                if next_at is None:
                    # Nothing scheduled at all; idle briefly and recheck.
                    self._stop.wait(1.0)
                    continue
                sleep_for = (next_at - now).total_seconds()
                if sleep_for > 0:
                    # Wake at most a second late on stop(); don't oversleep.
                    self._stop.wait(min(sleep_for, 60.0))
                continue
            self._fire(pending)
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                break

    def stop(self) -> None:
        """Signal :meth:`run` to return."""
        self._stop.set()

    def reset(self) -> None:
        """Clear the stop flag so ``run`` can be called again."""
        self._stop.clear()