"""retryflow: one decent way to retry transiently-failing operations.

The core idea: take an operation that might fail transiently (an HTTP request,
a flaky database connection, a subprocess that times out) and retry it with a
sensible backoff until it either succeeds or you decide to give up -- at which
point you get the real failure back, not a swallowed error.

Two ways to reach for it:

    # 1. As a decorator on an existing function -- the easy path.
    from retryflow import retry

    @retry(max_attempts=4, retry_on=ConnectionError)
    def fetch(url):
        return requests.get(url).json()

    # 2. Inline, when you don't own the function.
    from retryflow import retry

    result = retry(max_attempts=4, retry_on=ConnectionError)(do_the_thing)()

See the module docstrings and README for the full surface.
"""

from .core import (
    RetryError,
    retry,
    retry_on_exceptions,
    retry_if_exception_type,
    retry_if_message_matches,
    compose_retry_conditions,
)

__all__ = [
    "RetryError",
    "retry",
    "retry_on_exceptions",
    "retry_if_exception_type",
    "retry_if_message_matches",
    "compose_retry_conditions",
]

__version__ = "0.1.0"