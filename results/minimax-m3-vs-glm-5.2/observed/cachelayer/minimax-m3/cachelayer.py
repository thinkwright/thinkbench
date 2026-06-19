"""cachelayer — a small, honest memoization layer for expensive calls.

Public surface:

    from cachelayer import cache

    @cache
    def fetch_user(user_id):
        return requests.get(...).json()

    @cache(maxsize=256)
    def heavy_compute(x, y, *, mode="fast"):
        ...

A repeat call with the same positional and keyword arguments returns the
previously computed result without re-entering the wrapped function. The
cache is bounded (LRU eviction) so it cannot grow without bound.

Design notes:

* Keys are derived from positional and keyword arguments. Hashable inputs
  only — unhashable inputs raise TypeError, matching functools.lru_cache.
* Returned values are stored by reference. There is no serialization and
  no time-based expiry: a cached answer is exactly the object the wrapped
  function last returned for those inputs. If you need staleness semantics,
  layer them on top.
* The cache is thread-safe via a single re-entrant lock. This is the
  common case for "expensive lookup" wrappers and keeps the implementation
  small. It is not safe to call the wrapped function re-entrantly from
  within itself under the same cache (don't decorate recursive functions).
"""

from __future__ import annotations

from collections import OrderedDict
from functools import wraps
from threading import RLock
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

__all__ = ["cache", "Cache"]


# A single module-level sentinel used to separate positional args from
# keyword args inside a cache key. Must be a singleton so that two calls
# with the same logical arguments produce equal keys.
_KW_SENTINEL = object()


def _make_key(args: tuple, kwargs: dict) -> tuple:
    """Build a hashable cache key from call arguments.

    Positional args come first, then a sentinel, then kwargs as sorted
    (name, value) pairs. This matches the semantics of functools._make_key
    without depending on a private API.
    """
    if kwargs:
        return args + (_KW_SENTINEL,) + tuple(sorted(kwargs.items()))
    return args


class Cache:
    """A bounded LRU cache. Exposed for users who want to wrap manually."""

    __slots__ = ("_data", "_maxsize", "_lock", "hits", "misses")

    def __init__(self, maxsize: int = 1024) -> None:
        if maxsize < 0:
            raise ValueError("maxsize must be non-negative")
        self._data: "OrderedDict[tuple, Any]" = OrderedDict()
        self._maxsize = maxsize
        self._lock = RLock()
        self.hits = 0
        self.misses = 0

    def __repr__(self) -> str:
        return f"Cache(maxsize={self._maxsize}, size={len(self._data)})"

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: tuple) -> Any:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self.hits += 1
                return self._data[key]
            self.misses += 1
            return _MISS

    def put(self, key: tuple, value: Any) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self._data[key] = value
                return
            self._data[key] = value
            if len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self.hits = 0
            self.misses = 0


_MISS = object()


def cache(func_or_maxsize=None, *, maxsize: int = 1024):
    """Decorate a function with a bounded LRU cache.

    Both forms are supported::

        @cache
        def f(x): ...

        @cache(maxsize=256)
        def f(x): ...
    """
    # Bare @cache: func_or_maxsize is the function itself.
    if callable(func_or_maxsize) and not isinstance(func_or_maxsize, type):
        return _make_wrapper(func_or_maxsize, Cache(maxsize=maxsize))

    # Parameterized @cache(maxsize=N): func_or_maxsize is the maxsize value.
    if func_or_maxsize is not None:
        maxsize = func_or_maxsize

    def decorator(func: F) -> F:
        return _make_wrapper(func, Cache(maxsize=maxsize))

    return decorator


def _make_wrapper(func: F, store: Cache) -> F:
    @wraps(func)
    def wrapper(*args, **kwargs):
        key = _make_key(args, kwargs)
        cached = store.get(key)
        if cached is not _MISS:
            return cached
        value = func(*args, **kwargs)
        store.put(key, value)
        return value

    wrapper.cache = store  # type: ignore[attr-defined]
    wrapper.cache_clear = store.clear  # type: ignore[attr-defined]
    return wrapper  # type: ignore[return-value]
