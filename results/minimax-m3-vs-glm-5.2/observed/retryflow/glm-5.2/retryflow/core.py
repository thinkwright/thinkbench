"""Core retry logic for retryflow.

The design goals, in priority order:

1. Easy to reach for on existing code. The primary surface is a decorator,
   ``retry(...)``, that wraps a callable and retries it. It also works inline as
   ``retry(...)(fn)``.

2. Predictable outcomes. When retries are exhausted, the *last* exception is
   re-raised unchanged. Nothing is swallowed. If you ask for a result and the
   operation never succeeds, you get the real failure.

3. Selective retrying. Not every failure is worth retrying. ``retry_on`` takes a
   predicate ``(attempt, exception) -> bool``; helpers build common predicates
   (by exception type, by message substring, combinations). Returning False from
   the predicate stops retrying immediately and re-raises.

4. Sensible backoff. Exponential backoff with optional jitter, capped by
   ``max_delay``. Stops after ``max_attempts`` and/or ``max_elapsed`` seconds.

5. Observable. An optional ``on_retry`` callback is invoked before each retry
   with ``(attempt, exception, delay)`` so callers can log/metric without
   poking at internals.

6. Testable. ``sleep`` is injectable so tests don't actually wait.
"""

from __future__ import annotations

import functools
import random
import time
from typing import Any, Callable, Optional, Tuple, Type

__all__ = [
    "RetryError",
    "retry",
    "retry_on_exceptions",
    "retry_if_exception_type",
    "retry_if_message_matches",
    "compose_retry_conditions",
]


class RetryError(Exception):
    """Raised only when a retry condition predicate itself misbehaves.

    The normal "gave up" path re-raises the operation's last exception, so you
    should not normally see this. It exists so that a buggy ``retry_on``
    predicate (one that raises) surfaces loudly rather than being mistaken for
    a retryable failure of the wrapped operation.
    """


# --- retry condition helpers ------------------------------------------------

def retry_if_exception_type(
    *exceptions: Type[BaseException],
) -> Callable[[int, BaseException], bool]:
    """Build a retry condition that retries only on given exception types.

    A failure whose exception is an instance of one of ``exceptions`` is
    retryable; anything else stops immediately and is re-raised.

        @retry(retry_on=retry_if_exception_type(ConnectionError, TimeoutError))
        def fetch(): ...
    """
    if not exceptions:
        raise ValueError("retry_if_exception_type requires at least one exception type")
    types = tuple(exceptions)

    def condition(_attempt: int, exc: BaseException) -> bool:
        return isinstance(exc, types)

    condition.__name__ = f"retry_if_exception_type({', '.join(t.__name__ for t in types)})"
    return condition


# A short, memorable alias.
retry_on_exceptions = retry_if_exception_type


def retry_if_message_matches(
    *substrings: str,
) -> Callable[[int, BaseException], bool]:
    """Build a retry condition that retries when the exception message contains
    any of the given substrings (case-insensitive).

    Useful when a library raises a generic exception type but puts the
    distinguishing detail in the message, e.g. ``retry_if_message_matches(
    "unreachable", "temporarily")``.
    """
    if not substrings:
        raise ValueError("retry_if_message_matches requires at least one substring")
    lowered = tuple(s.lower() for s in substrings)

    def condition(_attempt: int, exc: BaseException) -> bool:
        msg = str(exc).lower()
        return any(s in msg for s in lowered)

    condition.__name__ = f"retry_if_message_matches({', '.join(repr(s) for s in substrings)})"
    return condition


def compose_retry_conditions(
    *conditions: Callable[[int, BaseException], bool],
) -> Callable[[int, BaseException], bool]:
    """Combine retry conditions with OR semantics.

    The operation is retryable if *any* condition says it is. This lets you mix,
    e.g., a type-based rule with a message-based rule:

        retry_on=compose_retry_conditions(
            retry_if_exception_type(ConnectionError),
            retry_if_message_matches("temporarily unavailable"),
        )
    """
    if not conditions:
        raise ValueError("compose_retry_conditions requires at least one condition")
    conds = tuple(conditions)

    def condition(attempt: int, exc: BaseException) -> bool:
        return any(c(attempt, exc) for c in conds)

    condition.__name__ = "compose_retry_conditions(" + ", ".join(
        getattr(c, "__name__", repr(c)) for c in conds
    ) + ")"
    return condition


# --- the decorator ----------------------------------------------------------

def retry(
    *,
    max_attempts: int = 3,
    base_delay: float = 0.1,
    max_delay: float = 30.0,
    jitter: bool = True,
    max_elapsed: Optional[float] = None,
    retry_on: Optional[Callable[[int, BaseException], bool]] = None,
    on_retry: Optional[Callable[[int, BaseException, float], None]] = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Return a decorator that retries a callable on transient failures.

    Parameters
    ----------
    max_attempts:
        Maximum number of attempts in total, including the first. Must be >= 1.
        With ``max_attempts=3`` the operation runs up to 3 times.
    base_delay:
        Delay before the first retry, in seconds. Subsequent delays grow
        exponentially: ``base_delay * 2**(attempt-1)``, capped at ``max_delay``.
        Set to 0 to retry with no delay.
    max_delay:
        Upper bound on the computed delay between attempts, in seconds.
    jitter:
        If True (default), multiply each delay by a random factor in [0.5, 1.0)
        to spread out concurrent retries. No jitter when False -- delays are
        exactly exponential, which is handy for tests.
    max_elapsed:
        If set, give up once this many seconds have elapsed since the first
        attempt began (wall-clock via ``clock``). The current attempt still
        completes; the limit is checked before each retry.
    retry_on:
        Predicate ``(attempt, exception) -> bool``. If it returns True the
        failure is retryable (subject to the stop conditions); if False the
        exception is re-raised immediately. Defaults to "retry any Exception".
        Use ``retry_if_exception_type`` / ``retry_if_message_matches`` /
        ``compose_retry_conditions`` to build selective rules.
    on_retry:
        Optional callback ``(attempt, exception, delay)`` invoked just before
        sleeping before a retry. ``attempt`` is the 1-based number of the
        attempt that just failed. Useful for logging/metrics.
    sleep:
        Function used to wait between attempts. Defaults to ``time.sleep``;
        inject a fake for tests.
    clock:
        Monotonic clock used for ``max_elapsed``. Defaults to
        ``time.monotonic``.

    Outcome
    -------
    On success, the wrapped callable's return value is returned.
    On exhaustion (stop conditions hit while still failing), the *last*
    exception raised by the operation is re-raised unchanged.
    On a non-retryable failure (``retry_on`` returns False), that exception is
    re-raised immediately.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    if base_delay < 0:
        raise ValueError("base_delay must be >= 0")
    if max_delay < 0:
        raise ValueError("max_delay must be >= 0")
    if max_elapsed is not None and max_elapsed < 0:
        raise ValueError("max_elapsed must be >= 0 or None")

    if retry_on is None:
        retry_on = _retry_any_exception

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = clock()
            attempt = 0
            last_exc: Optional[BaseException] = None
            while True:
                attempt += 1
                try:
                    return fn(*args, **kwargs)
                except BaseException as exc:  # noqa: BLE001 - intentional, we re-raise
                    last_exc = exc

                    # Should we even consider retrying this failure?
                    try:
                        retryable = retry_on(attempt, exc)
                    except Exception as predicate_exc:  # noqa: BLE001
                        # A buggy predicate must not masquerade as a retryable
                        # operation failure.
                        raise RetryError(
                            f"retry_on predicate raised on attempt {attempt}: "
                            f"{predicate_exc!r}"
                        ) from predicate_exc

                    if not retryable:
                        raise

                    # Are we out of attempts?
                    if attempt >= max_attempts:
                        raise

                    # Are we out of time?
                    if max_elapsed is not None:
                        elapsed = clock() - start
                        if elapsed >= max_elapsed:
                            raise

                    # Compute the delay before the next attempt.
                    delay = _compute_delay(
                        attempt=attempt,
                        base_delay=base_delay,
                        max_delay=max_delay,
                        jitter=jitter,
                    )

                    if on_retry is not None:
                        on_retry(attempt, exc, delay)

                    if delay > 0:
                        sleep(delay)

        wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
        return wrapper

    return decorator


def _retry_any_exception(_attempt: int, exc: BaseException) -> bool:
    # Default policy: retry any Exception, but not BaseException subclasses
    # like KeyboardInterrupt / SystemExit -- those should propagate.
    return isinstance(exc, Exception)


def _compute_delay(
    *,
    attempt: int,
    base_delay: float,
    max_delay: float,
    jitter: bool,
) -> float:
    """Exponential backoff for the delay *before* the next attempt.

    ``attempt`` is the 1-based number of the attempt that just failed, so the
    delay before the 1st retry is ``base_delay * 2**0``.
    """
    if base_delay <= 0:
        return 0.0
    # attempt is the just-failed attempt number; the first retry happens after
    # attempt 1, so exponent is attempt - 1.
    exp = attempt - 1
    # Guard against overflow for huge attempt counts.
    if exp > 62:
        delay = float("inf")
    else:
        delay = base_delay * (2 ** exp)
    if delay > max_delay:
        delay = max_delay
    if jitter:
        delay *= random.uniform(0.5, 1.0)
    return delay