"""Tests for retryflow. Run with: python -m pytest -q"""

from __future__ import annotations

import time
from typing import List

import pytest

import retryflow
from retryflow import RetryPolicy, retry, run


# --- helpers ---------------------------------------------------------------

class FlakyError(Exception):
    pass


class FatalError(Exception):
    pass


def make_flaky(fail_times: int, exc=FlakyError):
    """Return a function that raises ``exc`` for the first ``fail_times``
    calls, then returns 'ok'."""
    state = {"calls": 0}

    def f():
        state["calls"] += 1
        if state["calls"] <= fail_times:
            raise exc
        return "ok"
    f.state = state  # type: ignore[attr-defined]
    return f


# --- policy validation -----------------------------------------------------

def test_policy_rejects_bad_max_attempts():
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)


def test_policy_rejects_negative_delay():
    with pytest.raises(ValueError):
        RetryPolicy(base_delay=-1.0)


def test_policy_rejects_multiplier_below_one():
    with pytest.raises(ValueError):
        RetryPolicy(multiplier=0.5)


def test_policy_clamps_max_delay_below_base():
    p = RetryPolicy(base_delay=5.0, max_delay=1.0)
    assert p.max_delay == 5.0


# --- success path ----------------------------------------------------------

def test_succeeds_on_first_try():
    f = make_flaky(fail_times=0)
    assert run(f, policy=RetryPolicy(retry_on=FlakyError)) == "ok"
    assert f.state["calls"] == 1


def test_succeeds_after_retries():
    f = make_flaky(fail_times=2)
    p = RetryPolicy(max_attempts=5, base_delay=0.0, retry_on=FlakyError)
    assert run(f, policy=p) == "ok"
    assert f.state["calls"] == 3


# --- failure path: caller sees the real exception -------------------------

def test_non_retryable_raises_immediately():
    f = make_flaky(fail_times=10, exc=FatalError)
    p = RetryPolicy(max_attempts=5, retry_on=FlakyError)
    with pytest.raises(FatalError):
        run(f, policy=p)
    assert f.state["calls"] == 1, "non-retryable must not be retried"


def test_exhausted_attempts_raises_last_exception():
    f = make_flaky(fail_times=10)
    p = RetryPolicy(max_attempts=3, base_delay=0.0, retry_on=FlakyError)
    with pytest.raises(FlakyError):
        run(f, policy=p)
    assert f.state["calls"] == 3


def test_default_policy_retries_nothing():
    """The default policy must not silently retry — that's the whole point of
    forcing the caller to opt in."""
    f = make_flaky(fail_times=10)
    with pytest.raises(FlakyError):
        run(f, policy=RetryPolicy())
    assert f.state["calls"] == 1


def test_max_attempts_one_means_no_retry():
    f = make_flaky(fail_times=10)
    with pytest.raises(FlakyError):
        run(f, policy=RetryPolicy(max_attempts=1, retry_on=FlakyError))
    assert f.state["calls"] == 1


# --- retry_on: types, tuples, predicates ----------------------------------

def test_retry_on_tuple_of_types():
    f = make_flaky(fail_times=2, exc=TimeoutError)
    p = RetryPolicy(max_attempts=5, base_delay=0.0,
                    retry_on=(FlakyError, TimeoutError))
    assert run(f, policy=p) == "ok"


def test_retry_on_predicate():
    class HTTPError(Exception):
        def __init__(self, status):
            super().__init__(status)
            self.status = status

    state = {"calls": 0}

    def f():
        state["calls"] += 1
        if state["calls"] == 1:
            raise HTTPError(503)
        if state["calls"] == 2:
            raise HTTPError(404)  # not retryable
        return "ok"

    is_5xx = lambda exc: isinstance(exc, HTTPError) and 500 <= exc.status < 600  # noqa: E731
    p = RetryPolicy(max_attempts=5, base_delay=0.0, retry_on=is_5xx)
    with pytest.raises(HTTPError) as info:
        run(f, policy=p)
    assert info.value.status == 404
    assert state["calls"] == 2


def test_retry_on_subclass_match():
    class ChildError(FlakyError):
        pass

    f = make_flaky(fail_times=2, exc=ChildError)
    p = RetryPolicy(max_attempts=5, base_delay=0.0, retry_on=FlakyError)
    assert run(f, policy=p) == "ok"


# --- backoff ---------------------------------------------------------------

def test_deterministic_delays_grow_exponentially():
    p = RetryPolicy(base_delay=1.0, multiplier=2.0, max_delay=100.0,
                    jitter=False, retry_on=FlakyError)
    # attempt 2 -> 1.0, attempt 3 -> 2.0, attempt 4 -> 4.0
    assert p.delay_for(2) == 1.0
    assert p.delay_for(3) == 2.0
    assert p.delay_for(4) == 4.0


def test_delay_capped_by_max_delay():
    p = RetryPolicy(base_delay=1.0, multiplier=10.0, max_delay=5.0,
                    jitter=False)
    assert p.delay_for(5) == 5.0  # would be 1000 without cap


def test_jittered_delays_are_in_range():
    p = RetryPolicy(base_delay=2.0, multiplier=2.0, max_delay=8.0,
                    jitter=True)
    for _ in range(200):
        d = p.delay_for(3)  # raw = 4.0, capped = 4.0
        assert 0.0 <= d <= 4.0


def test_no_sleep_on_first_attempt():
    p = RetryPolicy(base_delay=10.0, jitter=False)
    assert p.delay_for(1) == 0.0


def test_actual_sleeps_happen(monkeypatch):
    """Verify the loop actually sleeps between attempts."""
    sleeps: List[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    f = make_flaky(fail_times=2)
    p = RetryPolicy(max_attempts=5, base_delay=0.5, jitter=False,
                    retry_on=FlakyError)
    run(f, policy=p)
    # Two failures -> two sleeps, before attempts 2 and 3.
    assert sleeps == [0.5, 1.0]


# --- on_retry callback -----------------------------------------------------

def test_on_retry_called_with_attempt_exc_delay(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    calls: List[tuple] = []

    def cb(attempt, exc, delay):
        calls.append((attempt, type(exc).__name__, delay))

    f = make_flaky(fail_times=2)
    p = RetryPolicy(max_attempts=5, base_delay=0.5, jitter=False,
                    retry_on=FlakyError, on_retry=cb)
    run(f, policy=p)
    assert calls == [(1, "FlakyError", 0.5), (2, "FlakyError", 1.0)]


def test_on_retry_exception_is_swallowed(monkeypatch):
    """A misbehaving callback must not change retry behavior."""
    monkeypatch.setattr(time, "sleep", lambda s: None)

    def bad_cb(attempt, exc, delay):
        raise RuntimeError("logging is broken")

    f = make_flaky(fail_times=2)
    p = RetryPolicy(max_attempts=5, base_delay=0.0, retry_on=FlakyError,
                    on_retry=bad_cb)
    assert run(f, policy=p) == "ok"


# --- decorator -------------------------------------------------------------

def test_decorator_with_policy():
    @retry(policy=RetryPolicy(max_attempts=5, base_delay=0.0,
                              retry_on=FlakyError))
    def f():
        f.calls += 1  # type: ignore[attr-defined]
        if f.calls < 3:  # type: ignore[attr-defined]
            raise FlakyError
        return "ok"
    f.calls = 0  # type: ignore[attr-defined]
    assert f() == "ok"
    assert f.calls == 3  # type: ignore[attr-defined]


def test_decorator_with_kwargs():
    @retry(retry_on=FlakyError, max_attempts=4, base_delay=0.0)
    def f():
        f.calls += 1  # type: ignore[attr-defined]
        if f.calls < 2:  # type: ignore[attr-defined]
            raise FlakyError
        return "ok"
    f.calls = 0  # type: ignore[attr-defined]
    assert f() == "ok"


def test_decorator_preserves_metadata():
    @retry(retry_on=FlakyError)
    def my_function():
        """important docstring"""
        return 1
    assert my_function.__name__ == "my_function"
    assert my_function.__doc__ == "important docstring"


def test_decorator_propagates_non_retryable():
    @retry(retry_on=FlakyError, max_attempts=5, base_delay=0.0)
    def f():
        f.calls += 1  # type: ignore[attr-defined]
        raise FatalError
    f.calls = 0  # type: ignore[attr-defined]
    with pytest.raises(FatalError):
        f()
    assert f.calls == 1  # type: ignore[attr-defined]


# --- return values pass through -------------------------------------------

def test_return_value_passes_through():
    sentinel = object()
    p = RetryPolicy(retry_on=FlakyError)
    assert run(lambda: sentinel, policy=p) is sentinel


def test_args_and_kwargs_pass_through():
    seen = {}

    def f(a, b, *, c):
        seen["args"] = (a, b)
        seen["kwargs"] = {"c": c}
        return a + b + c

    p = RetryPolicy(retry_on=FlakyError)
    assert run(f, 1, 2, c=3, policy=p) == 6
    assert seen == {"args": (1, 2), "kwargs": {"c": 3}}
