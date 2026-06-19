"""ratelimit — a small token-bucket rate limiter.

A `Limiter` decides, for a given key (API key, user id, IP, ...), whether a
request is allowed right now. Each key gets its own bucket, so noisy callers
don't starve quiet ones.

The algorithm is a token bucket: each bucket holds at most `capacity` tokens
and refills at `rate` tokens per second. A request consumes one token. If the
bucket is empty, the request is denied.

Token buckets behave predictably under bursts: a caller can spend up to
`capacity` tokens back-to-back, then is limited to `rate` per second
afterwards. The limit you configure is the limit you actually observe.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Decision:
    """The outcome of a single rate-limit check.

    Carries enough information to explain *why* a decision came out the way
    it did, not just yes/no.
    """

    allowed: bool
    key: str
    limit: int            # configured capacity (max burst)
    rate: float           # configured refill rate (tokens/sec)
    remaining: float      # tokens left in the bucket after this check
    retry_after: float    # seconds until at least one token is available
                         # (0.0 when allowed)

    def __repr__(self) -> str:
        verdict = "allow" if self.allowed else "deny"
        return (
            f"Decision({verdict} key={self.key!r} "
            f"remaining={self.remaining:.3f} retry_after={self.retry_after:.3f}s)"
        )


class _Bucket:
    """One bucket, one key. Not thread-safe on its own; the Limiter locks."""

    __slots__ = ("tokens", "last_refill")

    def __init__(self, capacity: float, rate: float, now: float) -> None:
        # Start full so a brand-new caller can use their full burst budget
        # immediately rather than waiting for the bucket to fill.
        self.tokens = float(capacity)
        self.last_refill = now

    def refill(self, rate: float, capacity: float, now: float) -> None:
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(capacity, self.tokens + elapsed * rate)
            self.last_refill = now

    def take(self) -> bool:
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    def time_to_one(self, rate: float) -> float:
        if rate <= 0:
            return float("inf")
        deficit = 1.0 - self.tokens
        return max(0.0, deficit / rate)


class Limiter:
    """A thread-safe, in-memory token-bucket rate limiter.

    Parameters
    ----------
    capacity:
        Maximum tokens in a bucket, i.e. the maximum burst size.
    rate:
        Refill rate in tokens per second, i.e. the steady-state limit.
    clock:
        Callable returning the current time in seconds. Defaults to
        `time.monotonic`. Inject for tests.
    """

    def __init__(
        self,
        capacity: int,
        rate: float,
        *,
        clock=time.monotonic,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if rate <= 0:
            raise ValueError("rate must be positive")
        self._capacity = float(capacity)
        self._rate = float(rate)
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return int(self._capacity)

    @property
    def rate(self) -> float:
        return self._rate

    def check(self, key: str, cost: float = 1.0) -> Decision:
        """Decide whether `key` may proceed, consuming `cost` tokens.

        Returns a `Decision` describing the outcome.
        """
        if cost <= 0:
            raise ValueError("cost must be positive")
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(self._capacity, self._rate, now)
                self._buckets[key] = bucket
            bucket.refill(self._rate, self._capacity, now)

            if bucket.tokens >= cost:
                bucket.tokens -= cost
                return Decision(
                    allowed=True,
                    key=key,
                    limit=self.capacity,
                    rate=self._rate,
                    remaining=bucket.tokens,
                    retry_after=0.0,
                )

            # Denied. Compute how long until enough tokens accrue for `cost`.
            deficit = cost - bucket.tokens
            retry_after = deficit / self._rate
            return Decision(
                allowed=False,
                key=key,
                limit=self.capacity,
                rate=self._rate,
                remaining=bucket.tokens,
                retry_after=retry_after,
            )

    def reset(self, key: Optional[str] = None) -> None:
        """Forget history for one key, or all keys if `key` is None."""
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)
