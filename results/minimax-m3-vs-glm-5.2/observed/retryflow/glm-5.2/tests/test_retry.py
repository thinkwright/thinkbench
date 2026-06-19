"""Tests for retryflow's retry behavior.

These use an injectable fake sleep/clock so they run instantly and deterministically.
"""

import pytest

from retryflow import (
    RetryError,
    retry,
    retry_if_exception_type,
    retry_if_message_matches,
    compose_retry_conditions,
)
from retryflow.core import retry_on_exceptions


class FakeClock:
    """A controllable monotonic clock for max_elapsed tests."""

    def __init__(self, start=0.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class Flaky:
    """A callable that fails `fail_n` times then returns `value`."""

    def __init__(self, fail_n, exc=RuntimeError, value="ok", msg="boom"):
        self.fail_n = fail_n
        self.calls = 0
        self.exc = exc
        self.value = value
        self.msg = msg

    def __call__(self):
        self.calls += 1
        if self.calls <= self.fail_n:
            raise self.exc(self.msg)
        return self.value


# --- success / exhaustion ---------------------------------------------------

def test_succeeds_first_try_no_retry():
    f = Flaky(fail_n=0)
    sleeps = []
    out = retry(max_attempts=3, sleep=sleeps.append)(f)()
    assert out == "ok"
    assert f.calls == 1
    assert sleeps == []


def test_succeeds_after_retries():
    f = Flaky(fail_n=2)
    sleeps = []
    out = retry(max_attempts=4, base_delay=0.0, sleep=sleeps.append)(f)()
    assert out == "ok"
    assert f.calls == 3
    # 2 failures -> 2 sleeps (before attempt 2 and 3)
    assert len(sleeps) == 2


def test_exhausts_and_reraises_last_exception():
    f = Flaky(fail_n=10, exc=ValueError, msg="nope")
    sleeps = []
    with pytest.raises(ValueError, match="nope"):
        retry(max_attempts=3, base_delay=0.0, sleep=sleeps.append)(f)()
    assert f.calls == 3
    assert len(sleeps) == 2


def test_max_attempts_one_means_no_retry():
    f = Flaky(fail_n=1)
    sleeps = []
    with pytest.raises(RuntimeError):
        retry(max_attempts=1, base_delay=0.0, sleep=sleeps.append)(f)()
    assert f.calls == 1
    assert sleeps == []


# --- retry_on selectivity ---------------------------------------------------

def test_retry_on_exception_type_retries_matching():
    f = Flaky(fail_n=2, exc=ConnectionError)
    out = retry(
        max_attempts=4,
        base_delay=0.0,
        retry_on=retry_if_exception_type(ConnectionError),
    )(f)()
    assert out == "ok"
    assert f.calls == 3


def test_retry_on_exception_type_stops_immediately_on_non_matching():
    f = Flaky(fail_n=2, exc=PermissionError, msg="forbidden")
    sleeps = []
    with pytest.raises(PermissionError):
        retry(
            max_attempts=5,
            base_delay=0.0,
            sleep=sleeps.append,
            retry_on=retry_if_exception_type(ConnectionError, TimeoutError),
        )(f)()
    # Non-retryable -> no retries at all.
    assert f.calls == 1
    assert sleeps == []


def test_retry_on_exceptions_alias_works():
    f = Flaky(fail_n=1, exc=TimeoutError)
    out = retry(
        max_attempts=3,
        base_delay=0.0,
        retry_on=retry_on_exceptions(TimeoutError),
    )(f)()
    assert out == "ok"


def test_retry_if_message_matches():
    f = Flaky(fail_n=2, exc=RuntimeError, msg="host unreachable")
    out = retry(
        max_attempts=4,
        base_delay=0.0,
        retry_on=retry_if_message_matches("unreachable"),
    )(f)()
    assert out == "ok"
    assert f.calls == 3


def test_retry_if_message_matches_case_insensitive_and_negative():
    f = Flaky(fail_n=2, exc=RuntimeError, msg="YOU ARE NOT AUTHORIZED")
    with pytest.raises(RuntimeError):
        retry(
            max_attempts=4,
            base_delay=0.0,
            retry_on=retry_if_message_matches("unreachable", "temporarily"),
        )(f)()
    assert f.calls == 1


def test_compose_retry_conditions_or_semantics():
    # Type rule would reject it, but message rule accepts it.
    f = Flaky(fail_n=1, exc=RuntimeError, msg="temporarily unavailable")
    out = retry(
        max_attempts=3,
        base_delay=0.0,
        retry_on=compose_retry_conditions(
            retry_if_exception_type(ConnectionError),
            retry_if_message_matches("temporarily"),
        ),
    )(f)()
    assert out == "ok"


def test_default_retries_any_exception_but_not_baseexception():
    f = Flaky(fail_n=2, exc=KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt):
        retry(max_attempts=5, base_delay=0.0)(f)()
    assert f.calls == 1


# --- backoff timing ---------------------------------------------------------

def test_exponential_backoff_without_jitter():
    sleeps = []
    f = Flaky(fail_n=3)
    retry(
        max_attempts=4,
        base_delay=1.0,
        max_delay=100.0,
        jitter=False,
        sleep=sleeps.append,
    )(f)()
    # delays: before attempt2 = 1*2^0=1, attempt3 = 1*2^1=2, attempt4 = 1*2^2=4
    assert sleeps == [1.0, 2.0, 4.0]


def test_max_delay_caps_backoff():
    sleeps = []
    f = Flaky(fail_n=4)
    retry(
        max_attempts=5,
        base_delay=1.0,
        max_delay=3.0,
        jitter=False,
        sleep=sleeps.append,
    )(f)()
    # 1, 2, 4->capped to 3, ...
    assert sleeps == [1.0, 2.0, 3.0, 3.0]


def test_jitter_keeps_delay_in_bounds():
    sleeps = []
    f = Flaky(fail_n=3)
    retry(
        max_attempts=4,
        base_delay=2.0,
        max_delay=100.0,
        jitter=True,
        sleep=sleeps.append,
    )(f)()
    # Without jitter: 2, 4, 8. With jitter factor in [0.5, 1.0).
    for got, raw in zip(sleeps, [2.0, 4.0, 8.0]):
        assert raw * 0.5 <= got < raw


def test_base_delay_zero_means_no_sleep():
    sleeps = []
    f = Flaky(fail_n=3)
    retry(max_attempts=4, base_delay=0.0, sleep=sleeps.append)(f)()
    assert sleeps == []


# --- max_elapsed ------------------------------------------------------------

def test_max_elapsed_stops_retrying():
    clock = FakeClock(start=0.0)

    def sleep(dt):
        clock.advance(dt)

    f = Flaky(fail_n=10)
    with pytest.raises(RuntimeError):
        retry(
            max_attempts=100,
            base_delay=5.0,
            max_elapsed=10.0,
            jitter=False,
            sleep=sleep,
            clock=clock,
        )(f)()
    # attempt1 fails, sleep 5 (elapsed=5 < 10, retry)
    # attempt2 fails, sleep 10 -> elapsed would be 15 >= 10, so stop before attempt3
    assert f.calls == 2


def test_max_elapsed_zero_retries_once_within_attempt():
    clock = FakeClock(start=0.0)
    sleeps = []
    f = Flaky(fail_n=10)
    with pytest.raises(RuntimeError):
        retry(
            max_attempts=100,
            base_delay=1.0,
            max_elapsed=0.0,
            jitter=False,
            sleep=sleeps.append,
            clock=clock,
        )(f)()
    # elapsed (0) >= max_elapsed (0) checked before first retry -> no retry
    assert f.calls == 1
    assert sleeps == []


# --- on_retry callback ------------------------------------------------------

def test_on_retry_callback_invoked():
    events = []
    f = Flaky(fail_n=2)

    def on_retry(attempt, exc, delay):
        events.append((attempt, type(exc).__name__, delay))

    retry(
        max_attempts=4,
        base_delay=1.0,
        jitter=False,
        on_retry=on_retry,
        sleep=lambda d: None,
    )(f)()
    assert events == [(1, "RuntimeError", 1.0), (2, "RuntimeError", 2.0)]


def test_on_retry_not_called_on_success():
    events = []
    f = Flaky(fail_n=0)
    retry(max_attempts=3, on_retry=lambda a, e, d: events.append(a))(f)()
    assert events == []


def test_on_retry_not_called_when_non_retryable():
    events = []
    f = Flaky(fail_n=2, exc=PermissionError)
    with pytest.raises(PermissionError):
        retry(
            max_attempts=5,
            base_delay=0.0,
            retry_on=retry_if_exception_type(ConnectionError),
            on_retry=lambda a, e, d: events.append(a),
        )(f)()
    assert events == []


# --- decorator usage & preservation -----------------------------------------

def test_decorator_preserves_metadata():
    @retry(max_attempts=3, base_delay=0.0)
    def fetch_thing(x):
        """Fetch a thing."""
        return x

    assert fetch_thing.__name__ == "fetch_thing"
    assert fetch_thing.__doc__ == "Fetch a thing."
    assert fetch_thing(42) == 42


def test_decorator_passes_args_kwargs():
    calls = []

    @retry(max_attempts=3, base_delay=0.0)
    def add(a, b, *, c=0):
        calls.append((a, b, c))
        if len(calls) < 2:
            raise ConnectionError("flake")
        return a + b + c

    assert add(1, 2, c=3) == 6
    assert calls == [(1, 2, 3), (1, 2, 3)]


def test_inline_usage_on_external_callable():
    f = Flaky(fail_n=1)
    wrapped = retry(max_attempts=3, base_delay=0.0)(f)
    assert wrapped() == "ok"
    assert f.calls == 2


# --- error handling of misconfiguration / bad predicates --------------------

def test_invalid_max_attempts():
    with pytest.raises(ValueError):
        retry(max_attempts=0)


def test_invalid_base_delay():
    with pytest.raises(ValueError):
        retry(base_delay=-1)


def test_invalid_max_elapsed():
    with pytest.raises(ValueError):
        retry(max_elapsed=-1)


def test_buggy_retry_on_predicate_raises_retry_error():
    def bad_predicate(attempt, exc):
        raise RuntimeError("predicate blew up")

    f = Flaky(fail_n=1)
    with pytest.raises(RetryError):
        retry(max_attempts=5, base_delay=0.0, retry_on=bad_predicate)(f)()


def test_helper_requires_args():
    with pytest.raises(ValueError):
        retry_if_exception_type()
    with pytest.raises(ValueError):
        retry_if_message_matches()
    with pytest.raises(ValueError):
        compose_retry_conditions()


# --- return value integrity -------------------------------------------------

def test_returns_value_not_wrapped():
    @retry(max_attempts=3, base_delay=0.0)
    def get():
        return {"a": 1, "b": [2, 3]}

    assert get() == {"a": 1, "b": [2, 3]}


def test_none_return_is_valid_success():
    @retry(max_attempts=3, base_delay=0.0)
    def do():
        return None

    assert do() is None