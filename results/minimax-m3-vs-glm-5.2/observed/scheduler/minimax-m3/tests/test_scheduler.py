"""Tests for the scheduling behavior.

We avoid sleeping in real time where possible by driving the Scheduler's
tick loop directly — `Scheduler._tick()` is the unit of work, and we can
call it with a controlled `now` by patching `datetime.now` inside the
core module.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, time as dtime, timedelta
from unittest.mock import patch

import pytest

from scheduler import DailyAt, Interval, Scheduler, Task


# ---------------------------------------------------------------------------
# Schedule math
# ---------------------------------------------------------------------------


def test_interval_next_after_is_offset():
    s = Interval(seconds=30)
    now = datetime(2024, 1, 1, 12, 0, 0)
    assert s.next_after(now) == now + timedelta(seconds=30)


def test_interval_rejects_non_positive():
    with pytest.raises(ValueError):
        Interval(seconds=0)
    with pytest.raises(ValueError):
        Interval(seconds=-1)


def test_daily_at_rolls_forward_when_time_has_passed():
    s = DailyAt(at=dtime(9, 0))
    now = datetime(2024, 1, 1, 10, 0, 0)  # already past 09:00 today
    nxt = s.next_after(now)
    assert nxt == datetime(2024, 1, 2, 9, 0, 0)


def test_daily_at_fires_today_when_time_is_still_ahead():
    s = DailyAt(at=dtime(23, 30))
    now = datetime(2024, 1, 1, 10, 0, 0)
    nxt = s.next_after(now)
    assert nxt == datetime(2024, 1, 1, 23, 30, 0)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_register_and_list():
    s = Scheduler()
    s.register(Task("a", lambda: None, Interval(seconds=10)))
    s.register(Task("b", lambda: None, DailyAt(at=dtime(8, 0))))
    assert [t.name for t in s.tasks()] == ["a", "b"]


def test_register_rejects_duplicate_names():
    s = Scheduler()
    s.register(Task("dup", lambda: None, Interval(seconds=1)))
    with pytest.raises(ValueError):
        s.register(Task("dup", lambda: None, Interval(seconds=1)))


def test_constructor_accepts_task_list():
    s = Scheduler([Task("x", lambda: None, Interval(seconds=5))])
    assert [t.name for t in s.tasks()] == ["x"]


# ---------------------------------------------------------------------------
# Tick behavior — the heart of "when it was due, it ran"
# ---------------------------------------------------------------------------


def test_tick_fires_due_task_and_records_run():
    calls = []
    s = Scheduler()
    s.register(Task("t", lambda: calls.append(1), Interval(seconds=10)))

    # First tick: nothing due (next run is ~10s out).
    s._tick()
    assert calls == []
    assert s.tasks()[0].run_count == 0

    # Advance the clock past the scheduled time and tick again.
    future = datetime.now() + timedelta(seconds=11)
    with patch("scheduler.core.datetime") as mock_dt:
        mock_dt.now.return_value = future
        s._tick()

    assert calls == [1]
    assert s.tasks()[0].run_count == 1
    assert s.tasks()[0].last_run == future


def test_tick_does_not_fire_task_not_yet_due():
    calls = []
    s = Scheduler()
    s.register(Task("t", lambda: calls.append(1), Interval(seconds=60)))
    s._tick()
    assert calls == []


def test_tick_fires_multiple_due_tasks_in_one_pass():
    calls = []
    s = Scheduler()
    s.register(Task("a", lambda: calls.append("a"), Interval(seconds=1)))
    s.register(Task("b", lambda: calls.append("b"), Interval(seconds=1)))

    # Force both to be due.
    future = datetime.now() + timedelta(seconds=5)
    with patch("scheduler.core.datetime") as mock_dt:
        mock_dt.now.return_value = future
        s._tick()

    assert sorted(calls) == ["a", "b"]


def test_slow_task_is_not_re_fired_in_same_tick():
    """If a task takes longer than the tick interval, it shouldn't be
    re-queued for the same tick — it should wait for its next scheduled
    time."""
    calls = []
    s = Scheduler()
    s.register(Task("slow", lambda: calls.append(1), Interval(seconds=1)))

    future = datetime.now() + timedelta(seconds=10)
    with patch("scheduler.core.datetime") as mock_dt:
        mock_dt.now.return_value = future
        s._tick()
        # Same "now" — a second tick in the same instant must not re-fire.
        s._tick()

    assert calls == [1]


def test_tick_swallows_task_exception_and_keeps_going():
    calls = []
    s = Scheduler()
    s.register(Task("bad", lambda: (_ for _ in ()).throw(RuntimeError("boom")),
                    Interval(seconds=1)))
    s.register(Task("good", lambda: calls.append(1), Interval(seconds=1)))

    future = datetime.now() + timedelta(seconds=5)
    with patch("scheduler.core.datetime") as mock_dt:
        mock_dt.now.return_value = future
        s._tick()

    assert calls == [1]


# ---------------------------------------------------------------------------
# Background thread — real timing, but bounded
# ---------------------------------------------------------------------------


def test_background_thread_fires_interval_task():
    """End-to-end: register, start, wait, observe a real run."""
    event = threading.Event()
    s = Scheduler()
    s.register(Task("ping", event.set, Interval(seconds=0.2)))
    s.start()
    try:
        assert event.wait(timeout=2.0), "task did not fire within 2s"
    finally:
        s.stop()
    assert s.tasks()[0].run_count >= 1


def test_start_is_idempotent_and_stop_is_safe():
    s = Scheduler()
    s.register(Task("t", lambda: None, Interval(seconds=60)))
    s.start()
    s.start()  # should not spawn a second thread
    assert s._thread is not None
    s.stop()
    s.stop()  # should not raise
    assert s._thread is None


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


def test_next_run_returns_scheduled_time():
    s = Scheduler()
    s.register(Task("t", lambda: None, DailyAt(at=dtime(9, 0))))
    nxt = s.next_run("t")
    assert nxt is not None
    assert nxt.time() == dtime(9, 0)


def test_next_run_unknown_task_returns_none():
    s = Scheduler()
    assert s.next_run("nope") is None
