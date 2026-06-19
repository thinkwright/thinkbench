"""Token-bucket rate limiter.

The core is :class:`Bucket`, a single caller's token bucket. :class:`Limiter`
owns a collection of buckets, one per caller, and is the thing you actually
call into. :class:`Decision` is the answer you get back, and it carries enough
state to explain why it came out the way it did.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional


class LimiterError(Exception):
    """Raised for misconfiguration of a :class:`Limiter` or :class:`Bucket`."""


def _monotonic() -> float:
    """Return a monotonic clock reading in seconds.

    Monotonic time is used deliberately: wall-clock time can jump backwards
    (NTP step, DST, manual change) and a rate limiter that trusts wall-clock
    time can hand out a free burst or stall forever when that happens.
    """
    return time.monotonic()


@dataclass(frozen=True)
class Decision:
    """The result of a single :meth:`Limiter.check` call.

    A ``Decision`` is the whole story of why a request was allowed or turned
    away, not just a yes/no. It's immutable and safe to log or hand back to a
    caller.

    Attributes:
        allowed: Whether the request may proceed.
        caller: The caller identifier the decision was made for.
        rate: The refill rate (tokens/second) in effect.
        capacity: The bucket capacity (max burst) in effect.
        tokens_remaining: Tokens left in the bucket *after* this check. For a
            denied request this is the (fractional) token count that was
            available, which is less than one.
        retry_after: Seconds the caller should wait before retrying, or
            ``None`` if the request was allowed. Always positive when present.
        checked_at: Monotonic-clock timestamp the decision was made at.
    """

    allowed: bool
    caller: str
    rate: float
    capacity: float
    tokens_remaining: float
    retry_after: Optional[float] = None
    checked_at: float = field(default_factory=_monotonic)

    def __str__(self) -> str:
        retry = f" retry_after={self.retry_after:.6f}" if self.retry_after is not None else ""
        return (
            f"allowed={self.allowed} caller={self.caller!r}"
            f" rate={self.rate} capacity={self.capacity}"
            f" tokens_remaining={self.tokens_remaining:.6f}"
            f" checked_at={self.checked_at:.6f}{retry}"
        )


class Bucket:
    """A single caller's token bucket.

    Holds at most ``capacity`` tokens and refills at ``rate`` tokens per
    second. ``take`` consumes one token if one is available and reports the
    outcome. All operations are guarded by the bucket's own lock, so it is safe
    to call from multiple threads.

    The bucket is lazy: it doesn't start the clock until the first ``take``,
    so a caller who never shows up costs nothing and a caller who shows up
    after a long idle gets a full burst (which is the intended behavior -- the
    bucket refills to capacity while idle).
    """

    __slots__ = ("rate", "capacity", "_tokens", "_last", "_lock")

    def __init__(self, rate: float, capacity: float) -> None:
        if rate <= 0:
            raise LimiterError(f"rate must be positive, got {rate!r}")
        if capacity <= 0:
            raise LimiterError(f"capacity must be positive, got {capacity!r}")
        self.rate = float(rate)
        self.capacity = float(capacity)
        # Start full so a fresh caller gets their full burst.
        self._tokens = float(capacity)
        # ``None`` means "not started yet"; set on first take.
        self._last: Optional[float] = None
        self._lock = threading.Lock()

    def _refill(self, now: float) -> None:
        """Bring ``_tokens`` up to date for the given monotonic time.

        Caller must hold ``_lock``. Tokens accrue at ``rate`` per second and
        are capped at ``capacity``. On the very first call this just records
        the start time (the bucket is already full).
        """
        if self._last is None:
            self._last = now
            return
        elapsed = now - self._last
        # Monotonic time shouldn't go backwards, but defend against it anyway:
        # a negative elapsed would *remove* tokens, which is never what we want.
        if elapsed <= 0:
            return
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last = now

    def take(self, now: float) -> Decision:
        """Attempt to consume one token at time ``now``.

        Returns a :class:`Decision` describing the outcome. ``now`` is taken as
        a parameter (rather than read inside) so a caller can drive the clock
        deterministically -- the tests rely on this.
        """
        with self._lock:
            self._refill(now)
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return Decision(
                    allowed=True,
                    caller="",  # filled in by the Limiter that owns us
                    rate=self.rate,
                    capacity=self.capacity,
                    tokens_remaining=self._tokens,
                    checked_at=now,
                )
            # Not enough tokens. Tell the caller how long until one is ready:
            # we need (1 - tokens) more, accruing at rate per second.
            deficit = 1.0 - self._tokens
            retry_after = deficit / self.rate
            return Decision(
                allowed=False,
                caller="",
                rate=self.rate,
                capacity=self.capacity,
                tokens_remaining=self._tokens,
                retry_after=retry_after,
                checked_at=now,
            )


class Limiter:
    """A rate limiter that tracks one :class:`Bucket` per caller.

    Construct it once with a policy (``rate`` and ``capacity``) and call
    :meth:`check` for each request, passing whatever identifies the caller --
    an API key, user id, IP, etc. Callers are isolated: each gets its own
    bucket, created on first use, so one noisy caller can't eat another's
    capacity.

    Args:
        rate: Sustained refill rate, in tokens (requests) per second. Must be
            positive.
        capacity: Bucket size, i.e. the largest burst a caller may make at
            once. Must be positive. Often set equal to ``rate`` for "1 second
            worth of burst" or higher to tolerate a short spike.
        clock: Optional zero-argument callable returning a monotonic time in
            seconds. Defaults to :func:`time.monotonic`. Override it in tests
            to drive the clock; in production leave it alone.
    """

    def __init__(
        self,
        rate: float,
        capacity: float,
        clock=time.monotonic,
    ) -> None:
        if rate <= 0:
            raise LimiterError(f"rate must be positive, got {rate!r}")
        if capacity <= 0:
            raise LimiterError(f"capacity must be positive, got {capacity!r}")
        self.rate = float(rate)
        self.capacity = float(capacity)
        self._clock = clock
        self._buckets: Dict[str, Bucket] = {}
        # A single lock guards the buckets dict. Each bucket has its own lock
        # for its own state, so two different callers never block each other
        # for longer than the dict lookup.
        self._lock = threading.Lock()

    def _bucket_for(self, caller: str) -> Bucket:
        # Fast path: bucket already exists. Lock only for the lookup-then-create
        # race; the actual take() happens outside this lock.
        bucket = self._buckets.get(caller)
        if bucket is not None:
            return bucket
        with self._lock:
            bucket = self._buckets.get(caller)
            if bucket is None:
                bucket = Bucket(self.rate, self.capacity)
                self._buckets[caller] = bucket
            return bucket

    def check(self, caller: str) -> Decision:
        """Decide whether ``caller`` may make a request right now.

        Returns a :class:`Decision`. If ``decision.allowed`` is False, the
        caller should be turned away and may retry after
        ``decision.retry_after`` seconds.

        ``caller`` must be a string (an empty string is treated as a distinct
        caller, not an error -- but you almost certainly want a real
        identifier).
        """
        if not isinstance(caller, str):
            raise LimiterError(
                f"caller must be a string, got {type(caller).__name__}"
            )
        now = self._clock()
        bucket = self._bucket_for(caller)
        decision = bucket.take(now)
        # Stamp the caller onto the decision (the bucket doesn't know it).
        return Decision(
            allowed=decision.allowed,
            caller=caller,
            rate=decision.rate,
            capacity=decision.capacity,
            tokens_remaining=decision.tokens_remaining,
            retry_after=decision.retry_after,
            checked_at=decision.checked_at,
        )