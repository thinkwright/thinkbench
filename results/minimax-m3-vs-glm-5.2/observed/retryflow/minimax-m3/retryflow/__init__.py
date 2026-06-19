"""retryflow — a small, predictable retry library.

Importable as ``retryflow``. Two ways to use it:

1. Decorate an existing function::

       @retryflow.retry(policy=retryflow.RetryPolicy(
           max_attempts=4,
           base_delay=0.5,
           retry_on=(ConnectionError, TimeoutError),
       ))
       def fetch(url):
           return http.get(url)

2. Wrap a callable dynamically::

       result = retryflow.run(
           lambda: db.connect(),
           policy=retryflow.RetryPolicy(max_attempts=3, retry_on=ConnectionError),
       )

The caller always sees the real outcome: either the function's return value, or
the last exception it raised (re-raised, not swallowed). See ``README.md`` for
the full usage notes.
"""

from .core import RetryPolicy, Retry, retry, run

__all__ = ["RetryPolicy", "Retry", "retry", "run"]
__version__ = "0.1.0"
