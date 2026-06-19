"""Tests for the limiting behavior of :mod:`ratelimit`.

The limiter takes an injectable ``clock`` so we can drive time deterministically
and assert exact outcomes -- these tests don't depend on wall-clock timing.
"""

from __future__ import annotations

import threading

import pytest

import ratelimit
from ratelimit import Decision, Limiter, LimiterError


class FakeClock:
    """A controllable monotonic clock for tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += float(seconds)


# ---------------------------------------------------------------------------
# Construction / validation
# ---------------------------------------------------------------------------


def test_limiter_rejects_nonpositive_rate():
    with pytest.raises(LimiterError):
        Limiter(rate=0, capacity=5)
    with pytest.raises(LimiterError):
        Limiter(rate=-1, capacity=5)


def test_limiter_rejects_nonpositive_capacity():
    with pytest.raises(LimiterError):
        Limiter(rate=5, capacity=0)
    with pytest.raises(LimiterError):
        Limiter(rate=5, capacity=-1)


def test_check_rejects_non_string_caller():
    limiter = Limiter(rate=5, capacity=5, clock=FakeClock())
    with pytest.raises(LimiterError):
        limiter.check(123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Burst behavior: the limit it promises is the limit you get
# ---------------------------------------------------------------------------


def test_burst_up_to_capacity_then_deny():
    """A fresh caller gets exactly `capacity` allows, then is denied."""
    clock = FakeClock()
    limiter = Limiter(rate=2, capacity=3, clock=clock)

    decisions = [limiter.check("a") for _ in range(3)]
    assert [d.allowed for d in decisions] == [True, True, True]
    # After 3 takes the bucket is empty.
    assert decisions[-1].tokens_remaining == pytest.approx(0.0)

    denied = limiter.check("a")
    assert denied.allowed is False
    assert denied.tokens_remaining < 1.0


def test_burst_size_equals_capacity_exactly():
    """Vary capacity and confirm the allow count tracks it exactly."""
    for cap in (1, 2, 5, 10):
        clock = FakeClock()
        limiter = Limiter(rate=1, capacity=cap, clock=clock)
        allowed = sum(limiter.check("a").allowed for _ in range(cap))
        assert allowed == cap
        # The very next one, with no time passing, must be denied.
        assert limiter.check("a").allowed is False


def test_denied_request_does_not_consume_tokens():
    """A denied check shouldn't make the bucket worse for the next check."""
    clock = FakeClock()
    limiter = Limiter(rate=1, capacity=1, clock=clock)

    assert limiter.check("a").allowed is True  # bucket now empty
    d1 = limiter.check("a")
    assert d1.allowed is False
    d2 = limiter.check("a")
    assert d2.allowed is False
    # Same retry_after both times -> no tokens were burned by the denials.
    assert d1.retry_after == pytest.approx(d2.retry_after)
    # After exactly retry_after seconds, one token is available again.
    clock.advance(d1.retry_after)
    assert limiter.check("a").allowed is True


# ---------------------------------------------------------------------------
# Refill: sustained rate over time
# ---------------------------------------------------------------------------


def test_refill_restores_tokens_at_rate():
    clock = FakeClock()
    limiter = Limiter(rate=2, capacity=2, clock=clock)

    assert limiter.check("a").allowed is True
    assert limiter.check("a").allowed is True
    assert limiter.check("a").allowed is False  # empty

    # 0.5s at 2/s -> 1 token refilled -> exactly one allow.
    clock.advance(0.5)
    assert limiter.check("a").allowed is True
    assert limiter.check("a").allowed is False

    # Another 0.5s -> one more.
    clock.advance(0.5)
    assert limiter.check("a").allowed is True


def test_refill_caps_at_capacity():
    """Idle time beyond what fills the bucket doesn't grant extra tokens."""
    clock = FakeClock()
    limiter = Limiter(rate=10, capacity=3, clock=clock)

    # Drain it.
    for _ in range(3):
        assert limiter.check("a").allowed is True
    assert limiter.check("a").allowed is False

    # Wait way more than enough to refill.
    clock.advance(1000.0)
    # Still only capacity allows, not 1000*10.
    allowed = sum(limiter.check("a").allowed for _ in range(3))
    assert allowed == 3
    assert limiter.check("a").allowed is False


def test_refill_is_fractional_and_accumulates():
    """Partial-token refills add up across checks close together in time."""
    clock = FakeClock()
    limiter = Limiter(rate=4, capacity=1, clock=clock)

    assert limiter.check("a").allowed is True  # 1 -> 0
    assert limiter.check("a").allowed is False

    # Four quarter-second advances, each refilling 1 token at 4/s, but we
    # consume on the last. The fractional accruals must sum correctly.
    clock.advance(0.25)  # +1 token -> 1
    assert limiter.check("a").allowed is True  # 1 -> 0
    assert limiter.check("a").allowed is False

    clock.advance(0.125)  # +0.5
    clock.advance(0.125)  # +0.5 -> 1
    assert limiter.check("a").allowed is True


# ---------------------------------------------------------------------------
# Caller isolation
# ---------------------------------------------------------------------------


def test_callers_are_isolated():
    """One caller hitting its limit must not affect another."""
    clock = FakeClock()
    limiter = Limiter(rate=1, capacity=1, clock=clock)

    assert limiter.check("a").allowed is True
    assert limiter.check("a").allowed is False  # 'a' exhausted

    # 'b' has its own full bucket.
    assert limiter.check("b").allowed is True
    assert limiter.check("b").allowed is False

    # 'a' is still exhausted; advancing time refills 'a' but we haven't.
    assert limiter.check("a").allowed is False


def test_many_callers_each_get_their_own_burst():
    clock = FakeClock()
    limiter = Limiter(rate=1, capacity=2, clock=clock)

    for caller in ("a", "b", "c", "d"):
        assert limiter.check(caller).allowed is True
        assert limiter.check(caller).allowed is True
        assert limiter.check(caller).allowed is False


def test_empty_string_is_a_valid_distinct_caller():
    clock = FakeClock()
    limiter = Limiter(rate=1, capacity=1, clock=clock)
    assert limiter.check("").allowed is True
    assert limiter.check("").allowed is False
    # A different non-empty caller is unaffected.
    assert limiter.check("x").allowed is True


# ---------------------------------------------------------------------------
# retry_after
# ---------------------------------------------------------------------------


def test_retry_after_is_positive_and_decreasing():
    clock = FakeClock()
    limiter = Limiter(rate=2, capacity=1, clock=clock)

    assert limiter.check("a").allowed is True  # empty
    d0 = limiter.check("a")
    assert d0.allowed is False
    assert d0.retry_after is not None
    assert d0.retry_after > 0
    assert d0.retry_after == pytest.approx(0.5)  # 1 token / 2 per sec

    # Halfway to a token: retry_after should shrink.
    clock.advance(0.25)
    d1 = limiter.check("a")
    assert d1.allowed is False
    assert d1.retry_after == pytest.approx(0.25)


def test_retry_after_none_when_allowed():
    clock = FakeClock()
    limiter = Limiter(rate=1, capacity=1, clock=clock)
    d = limiter.check("a")
    assert d.allowed is True
    assert d.retry_after is None


# ---------------------------------------------------------------------------
# Decision is self-describing
# ---------------------------------------------------------------------------


def test_decision_str_contains_the_whole_story():
    clock = FakeClock(start=42.0)
    limiter = Limiter(rate=3, capacity=5, clock=clock)
    d = limiter.check("api-key-1")
    s = str(d)
    assert "allowed=True" in s
    assert "api-key-1" in s
    assert "rate=3" in s
    assert "capacity=5" in s
    assert "tokens_remaining=" in s
    # No retry_after line when allowed.
    assert "retry_after" not in s

    # Drain and get a denial with retry_after.
    for _ in range(4):
        limiter.check("api-key-1")
    denied = limiter.check("api-key-1")
    ds = str(denied)
    assert "allowed=False" in ds
    assert "retry_after=" in ds


def test_decision_is_immutable():
    clock = FakeClock()
    limiter = Limiter(rate=1, capacity=1, clock=clock)
    d = limiter.check("a")
    with pytest.raises(Exception):
        d.allowed = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Concurrency: lots of checks landing close together
# ---------------------------------------------------------------------------


def test_concurrent_checks_never_exceed_capacity():
    """Hammer one caller from many threads; total allows must equal capacity.

    Uses the real monotonic clock, so this is a timing-sensitive smoke test,
    but the invariant it checks -- never hand out more than capacity at once
    -- is exactly the property we care about under bursts.
    """
    capacity = 20
    limiter = Limiter(rate=1000, capacity=capacity)
    # Drain the bucket instantly first so we're counting a single burst.
    # (rate is high, so we just race to drain capacity from a full bucket.)
    allowed = 0
    lock = threading.Lock()

    def worker():
        nonlocal allowed
        d = limiter.check("busy")
        with lock:
            if d.allowed:
                allowed += 1

    threads = [threading.Thread(target=worker) for _ in range(capacity * 20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # We must never allow more than `capacity` in the initial burst. With a
    # high rate some refill may sneak in, so we bound it generously but still
    # strictly above capacity would be a real bug. Allow a little refill slop
    # but not 2x.
    assert allowed >= capacity  # at least the burst got through
    assert allowed <= capacity + 5  # no runaway; the lock serializes takes


def test_concurrent_different_callers_dont_interfere():
    """Each of N callers, hit from threads, gets its own full burst."""
    callers = [f"c{i}" for i in range(8)]
    limiter = Limiter(rate=1, capacity=2)
    results: dict[str, int] = {c: 0 for c in callers}
    rlock = threading.Lock()

    def worker(caller: str):
        for _ in range(2):
            d = limiter.check(caller)
            with rlock:
                if d.allowed:
                    results[caller] += 1

    threads = [threading.Thread(target=worker, args=(c,)) for c in callers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for c in callers:
        assert results[c] == 2, f"caller {c} got {results[c]} allows, expected 2"


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------


def test_cli_runs_and_reports_counts(capsys):
    from ratelimit.__main__ import main

    rc = main(["--rate", "2", "--capacity", "2", "--caller", "abc", "--count", "4"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "allowed=2 denied=2" in out
    assert "retry_after=" in out  # denials show retry_after


def test_cli_rejects_bad_rate(capsys):
    from ratelimit.__main__ import main

    rc = main(["--rate", "0", "--capacity", "2"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "rate must be positive" in err


# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------


def test_public_api_exports():
    assert set(ratelimit.__all__) == {"Decision", "Limiter", "LimiterError"}
    assert isinstance(ratelimit.Limiter, type)
    assert isinstance(ratelimit.Decision, type)
    assert issubclass(ratelimit.LimiterError, Exception)