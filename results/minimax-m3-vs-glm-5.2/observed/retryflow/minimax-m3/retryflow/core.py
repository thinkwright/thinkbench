"""Core retry machinery for retryflow.

The public surface lives in ``retryflow.__init__``; this module holds the
implementation. Kept stdlib-only on purpose.
"""

from __future__ import annotations

import functools
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple, Type, Union

# A "retryable" spec is either:
#   - an exception class (or tuple of classes) — match by isinstance, or
#   - a callable predicate(exception) -> bool.
RetryableSpec = Union[Type[BaseException], Tuple[Type[BaseException], ...],
                      Callable[[BaseException], bool]]


def _matches(exc: BaseException, spec: Optional[RetryableSpec]) -> bool:
    """Return True if ``exc`` should be retried under ``spec``."""
    if spec is None:
        return False
    if isinstance(spec, type) and issubclass(spec, BaseException):
        return isinstance(exc, spec)
    if isinstance(spec, tuple) and all(
        isinstance(t, type) and issubclass(t, BaseException) for t in spec
    ):
        return isinstance(exc, spec)
    if callable(spec):
        return bool(spec(exc))
    # Defensive: an unrecognized spec is treated as "don't retry" rather than
    # silently retrying everything. The caller almost certainly made a mistake.
    return False


@dataclass(frozen=True)
class RetryPolicy:
    """How a single retried operation should behave.

    Attributes:
        max_attempts: Total attempts including the first try. Must be >= 1.
            ``1`` means "no retries" — useful as a default you can override.
        base_delay: Seconds to sleep before the second attempt. The actual
            sleep is bounded above by ``max_delay`` and jittered.
        max_delay: Upper bound on the sleep between attempts, in seconds.
        multiplier: Exponential growth factor applied to ``base_delay`` per
            attempt. ``2.0`` doubles each time.
        jitter: When True (default), sleep is drawn uniformly from
            ``[0, capped_delay]`` (full jitter). When False, sleep is exactly
            ``capped_delay`` (deterministic — handy for tests).
        retry_on: Which exceptions are worth retrying. ``None`` (the default)
            means "retry nothing" — you must opt in. Accepts an exception
            class, a tuple of classes, or a predicate ``exc -> bool``.
        on_retry: Optional callback invoked as ``on_retry(attempt, exc, delay)``
            after a failed attempt and before sleeping. Useful for logging.
    """

    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 30.0
    multiplier: float = 2.0
    jitter: bool = True
    retry_on: Optional[RetryableSpec] = None
    on_retry: Optional[Callable[[int, BaseException, float], None]] = None

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.base_delay < 0 or self.max_delay < 0:
            raise ValueError("delays must be non-negative")
        if self.multiplier < 1:
            raise ValueError("multiplier must be >= 1")
        if self.max_delay < self.base_delay:
            # Not an error, but surprising — clamp so behavior is predictable.
            object.__setattr__(self, "max_delay", self.base_delay)

    def delay_for(self, attempt: int) -> float:
        """Sleep duration before attempt number ``attempt`` (1-based).

        ``attempt=1`` is the first try and never sleeps; this method returns
        the delay *before* attempt N, for N >= 2.
        """
        if attempt < 2:
            return 0.0
        raw = self.base_delay * (self.multiplier ** (attempt - 2))
        capped = min(self.max_delay, raw)
        if self.jitter:
            return random.uniform(0.0, capped)
        return capped


class Retry:
    """A configured retrier. Use ``retryflow.Retry(policy=...)`` or the
    ``@retryflow.retry`` decorator, which builds one for you."""

    def __init__(self, policy: RetryPolicy) -> None:
        self.policy = policy

    def __call__(self, func: Callable, *args, **kwargs):
        """Run ``func(*args, **kwargs)`` under this retrier's policy.

        Returns the function's value on success. On failure, either re-raises
        the last exception (if attempts are exhausted or the exception isn't
        retryable) or sleeps and tries again.
        """
        policy = self.policy
        last_exc: Optional[BaseException] = None
        for attempt in range(1, policy.max_attempts + 1):
            try:
                return func(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001 — we re-raise below
                last_exc = exc
                if not _matches(exc, policy.retry_on):
                    # Not retryable: surface immediately, no swallowing.
                    raise
                if attempt >= policy.max_attempts:
                    # Out of attempts: surface the real error.
                    raise
                delay = policy.delay_for(attempt + 1)
                if policy.on_retry is not None:
                    try:
                        policy.on_retry(attempt, exc, delay)
                    except Exception:
                        # A misbehaving callback must not change retry behavior.
                        pass
                if delay > 0:
                    time.sleep(delay)
        # Unreachable: the loop either returns or raises. Kept as a safety net
        # so a future refactor can't accidentally return None on failure.
        assert last_exc is not None
        raise last_exc


def retry(
    func: Optional[Callable] = None,
    *,
    policy: Optional[RetryPolicy] = None,
    **policy_kwargs,
) -> Callable:
    """Decorator: ``@retryflow.retry`` or ``@retryflow.retry(policy=...)``.

    With no arguments, applies a default policy that retries nothing — the
    caller is expected to pass ``retry_on=...`` explicitly. This is deliberate:
    a silent "retry everything" default would mask real bugs.
    """
    if policy is None:
        policy = RetryPolicy(**policy_kwargs)
    retrier = Retry(policy)

    if func is not None and callable(func):
        # Bare ``@retryflow.retry`` form.
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return retrier(func, *args, **kwargs)
        return wrapper

    # ``@retryflow.retry(policy=...)`` form — return a decorator.
    def decorate(f: Callable) -> Callable:
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            return retrier(f, *args, **kwargs)
        return wrapper
    return decorate


def run(func: Callable, *, policy: RetryPolicy) -> object:
    """Run ``func()`` under ``policy`` without decorating it.

    Useful when the callable is built dynamically or you don't want to attach
    retry behavior to the function object itself.
    """
    return Retry(policy)(func)
