"""Tests for scheduling behavior."""

from __future__ import annotations

import datetime as dt
import threading
import time

import pytest

from scheduler import Scheduler, every, at, Schedule


UTC = dt.timezone.utc


def now_utc() -> dt.datetime:
    return dt.datetime.now(UTC)


# --- schedules ---------------------------------------------------------------


class TestInterval:
    def test_first_run_is_one_interval_from_now(self):
        sched = every(minutes=5)
        before = now_utc()
        nxt = sched.next_run(None)
        after = now_utc()
        delta = nxt - before
        assert dt.timedelta(minutes=5) <= delta <= (after - before) + dt.timedelta(minutes=5)

    def test_subsequent_runs_step_by_interval(self):
        sched = every(seconds=10)
        t0 = dt.datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        assert sched.next_run(t0) == t0 + dt.timedelta(seconds=10)
        assert sched.next_run(t0 + dt.timedelta(seconds=10)) == t0 + dt.timedelta(seconds=20)

    def test_units_compose(self):
        assert every(hours=1, minutes=30).total_seconds == 5400
        assert every(days=1).total_seconds == 86400
        assert every(weeks=1).total_seconds == 604800

    def test_zero_interval_rejected(self):
        with pytest.raises(ValueError):
            every()
        with pytest.raises(ValueError):
            every(minutes=0)

    def test_repr(self):
        assert repr(every(minutes=5)) == "every(minutes=5)"
        assert repr(every(hours=1, minutes=30)) == "every(hours=1, minutes=30)"


class TestDaily:
    def test_next_occurrence_today_if_future(self):
        # at 02:30 UTC; if "after" is 00:00, next is today 02:30
        sched = at("02:30")
        after = dt.datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        assert sched.next_run(after) == dt.datetime(2024, 1, 1, 2, 30, tzinfo=UTC)

    def test_next_occurrence_tomorrow_if_past(self):
        sched = at("02:30")
        after = dt.datetime(2024, 1, 1, 3, 0, tzinfo=UTC)
        assert sched.next_run(after) == dt.datetime(2024, 1, 2, 2, 30, tzinfo=UTC)

    def test_strictly_after(self):
        sched = at("02:30")
        after = dt.datetime(2024, 1, 1, 2, 30, tzinfo=UTC)
        # exactly at the time -> next day
        assert sched.next_run(after) == dt.datetime(2024, 1, 2, 2, 30, tzinfo=UTC)

    def test_repeats_daily(self):
        sched = at("02:30")
        t = dt.datetime(2024, 1, 1, 3, 0, tzinfo=UTC)
        times = []
        for _ in range(3):
            t = sched.next_run(t)
            times.append(t)
        assert times == [
            dt.datetime(2024, 1, 2, 2, 30, tzinfo=UTC),
            dt.datetime(2024, 1, 3, 2, 30, tzinfo=UTC),
            dt.datetime(2024, 1, 4, 2, 30, tzinfo=UTC),
        ]

    def test_timezone_honored(self):
        # 02:30 in UTC+2 is 00:30 UTC
        tz = dt.timezone(dt.timedelta(hours=2))
        sched = at("02:30", tz=tz)
        after = dt.datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        assert sched.next_run(after) == dt.datetime(2024, 1, 1, 0, 30, tzinfo=UTC)

    def test_invalid_time(self):
        with pytest.raises(ValueError):
            at("25:00")
        with pytest.raises(ValueError):
            at("2:99")
        with pytest.raises(ValueError):
            at("noon")


# --- scheduler ---------------------------------------------------------------


class TestScheduler:
    def test_add_returns_task_with_name(self):
        sched = Scheduler()

        def my_task():
            pass

        task = sched.add(every(seconds=10), my_task)
        assert task.name == "my_task"
        assert task.runs == 0
        assert task.last_run is None
        assert task in sched.tasks

    def test_run_once_fires_overdue(self):
        sched = Scheduler()
        fired = []
        # interval of 0 seconds isn't allowed; use a schedule that's already due
        class Now(Schedule):
            def next_run(self, after):
                base = after if after else now_utc()
                return base  # immediately due

        sched.add(Now(), lambda: fired.append(1))
        count = sched.run_once()
        assert count == 1
        assert fired == [1]

    def test_run_once_skips_not_yet_due(self):
        sched = Scheduler()
        fired = []
        sched.add(every(hours=1), lambda: fired.append(1))
        count = sched.run_once()
        assert count == 0
        assert fired == []

    def test_task_runs_and_reschedules(self):
        sched = Scheduler()
        calls = []

        class Twice(Schedule):
            """Due immediately, then one more time, then never."""

            def __init__(self):
                self.n = 0

            def next_run(self, after):
                if self.n >= 2:
                    return None
                self.n += 1
                base = after if after else now_utc()
                return base  # due now

        task = sched.add(Twice(), lambda: calls.append(task.runs))
        # First run_once: fires once (n becomes 1 during scheduling, but the
        # initial add already consumed one next_run). Walk through carefully:
        # add() -> next_run(None) [n=1, due now]
        # run_once fires -> _fire -> _schedule_next -> next_run [n=2, due now]
        sched.run_once()
        assert calls == [0]
        assert task.runs == 1
        # Second run_once fires the rescheduled one
        sched.run_once()
        assert task.runs == 2
        # Third: nothing left (next_run returns None)
        assert sched.run_once() == 0

    def test_exception_does_not_stop_scheduler(self):
        sched = Scheduler()
        good = []

        class Once(Schedule):
            def __init__(self):
                self.done = False

            def next_run(self, after):
                if self.done:
                    return None
                self.done = True
                return now_utc()

        def boom():
            raise RuntimeError("kaboom")

        sched.add(Once(), boom)
        sched.add(Once(), lambda: good.append(1))
        sched.run_once()
        assert good == [1]  # second task still ran despite first raising

    def test_remove_stops_future_firings(self):
        sched = Scheduler()
        fired = []

        class Always(Schedule):
            def next_run(self, after):
                base = after if after else now_utc()
                return base

        task = sched.add(Always(), lambda: fired.append(1))
        sched.run_once()
        assert fired == [1]
        sched.remove(task)
        assert sched.run_once() == 0
        assert fired == [1]

    def test_run_with_max_iterations(self):
        sched = Scheduler()
        fired = []

        class Always(Schedule):
            def next_run(self, after):
                base = after if after else now_utc()
                return base

        sched.add(Always(), lambda: fired.append(1))
        sched.run(max_iterations=3)
        assert len(fired) == 3

    def test_run_real_time_fires(self):
        """A real, wall-clock test: a 1-second interval fires within a timeout."""
        sched = Scheduler()
        fired = threading.Event()

        sched.add(every(seconds=1), fired.set)
        t = threading.Thread(target=sched.run, kwargs={"max_iterations": 1}, daemon=True)
        t.start()
        assert fired.wait(timeout=5), "task did not fire within 5s"
        t.join(timeout=5)
        assert not t.is_alive()

    def test_stop_from_another_thread(self):
        sched = Scheduler()
        sched.add(every(hours=1), lambda: None)  # nothing due soon
        t = threading.Thread(target=sched.run, daemon=True)
        t.start()
        time.sleep(0.1)
        sched.stop()
        t.join(timeout=2)
        assert not t.is_alive()