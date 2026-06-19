"""ratelimit -- a small, predictable rate limiter for putting in front of an API.

The model is a token bucket per caller. Each caller (API key, user id, IP, ...)
gets an independent bucket. A bucket holds at most ``capacity`` tokens and
refills at ``rate`` tokens per second. Every check consumes one token; if a
token is available the request is allowed, otherwise it is turned away.

This gives two things callers care about:

* a sustained rate of ``rate`` requests/second, averaged over time, and
* a bounded burst of up to ``capacity`` requests that land close together.

Different callers never interfere with each other -- each has its own bucket.

Example::

    import ratelimit

    limiter = ratelimit.Limiter(rate=10, capacity=10)
    decision = limiter.check("api-key-123")
    if not decision.allowed:
        ...  # turn the caller away, maybe with decision.retry_after

A decision explains itself::

    str(decision)
    # 'allowed=True caller="api-key-123" tokens_remaining=9.0 ...'

There's also a tiny CLI so you can watch a limit kick in without standing up
the whole service::

    $ python -m ratelimit --rate 2 --capacity 2 --caller abc --count 5
"""

from .limiter import Decision, Limiter, LimiterError

__all__ = ["Decision", "Limiter", "LimiterError"]
__version__ = "0.1.0"