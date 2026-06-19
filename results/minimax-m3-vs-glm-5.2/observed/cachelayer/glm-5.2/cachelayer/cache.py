"""Core cache implementation for cachelayer."""

import functools
from collections import OrderedDict
from typing import Any, Callable, Optional


class Cache:
    """A bounded, least-recently-used cache of function results.

    The cache keys on the arguments a function was called with. Two calls
    count as "the same" when their positional and keyword arguments are equal
    by value (see :func:`make_key`). When the cache is full, the
    least-recently-used entry is evicted before a new one is stored.

    A ``Cache`` can be used directly, or attached to a function with
    :func:`cache` / :func:`cached`.
    """

    def __init__(self, maxsize: int = 128, key: Optional[Callable[..., Any]] = None):
        if maxsize < 1:
            raise ValueError("maxsize must be a positive integer")
        self.maxsize = maxsize
        self._key = key or make_key
        self._data: "OrderedDict[Any, Any]" = OrderedDict()
        self.hits = 0
        self.misses = 0

    # -- size / inspection -------------------------------------------------

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: Any) -> bool:
        return key in self._data

    @property
    def size(self) -> int:
        """Number of entries currently stored."""
        return len(self._data)

    # -- core operations ---------------------------------------------------

    def get(self, key: Any) -> Any:
        """Return the cached value for ``key``, marking it recently used.

        Raises ``KeyError`` if absent. Use :meth:`lookup` for the
        compute-or-fetch flow.
        """
        self._data.move_to_end(key)
        return self._data[key]

    def store(self, key: Any, value: Any) -> None:
        """Store ``value`` under ``key``, evicting the LRU entry if full."""
        if key in self._data:
            self._data.move_to_end(key)
            self._data[key] = value
            return
        while len(self._data) >= self.maxsize:
            self._data.popitem(last=False)
        self._data[key] = value

    def lookup(self, key: Any, miss: Callable[[], Any]) -> Any:
        """Return the cached value for ``key``, or call ``miss()`` and cache it.

        ``miss`` is only called on a cache miss, so the expensive work happens
        at most once per distinct key.
        """
        try:
            value = self.get(key)
        except KeyError:
            value = miss()
            self.store(key, value)
            self.misses += 1
        else:
            self.hits += 1
        return value

    def clear(self) -> None:
        """Drop every cached entry."""
        self._data.clear()
        self.hits = 0
        self.misses = 0

    def evict(self, key: Any) -> None:
        """Remove a single entry, if present."""
        self._data.pop(key, None)

    # -- convenience -------------------------------------------------------

    def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Call ``func(*args, **kwargs)`` through this cache."""
        return self.lookup(self._key(args, kwargs), lambda: func(*args, **kwargs))


def make_key(args: tuple, kwargs: dict) -> Any:
    """Build a cache key from a call's positional and keyword arguments.

    Two calls produce the same key when their arguments are equal by value.
    Keyword order does not matter (``f(x=1, y=2)`` matches ``f(y=2, x=1)``),
    and unhashable values such as lists and dicts are compared by content
    rather than identity.

    The key is a hashable tuple, so it lives happily in the cache's ordered
    dict. Values that can't be made hashable here (e.g. custom objects without
    a stable ``repr``) should be handled with a custom ``key`` function.
    """
    return (_freeze(args), _freeze(sorted(kwargs.items())))


def _freeze(obj: Any) -> Any:
    """Return a hashable, value-based view of ``obj``.

    Containers are normalized so equal contents collapse to equal keys:
    lists become tuples, dicts become sorted tuples of pairs, sets become
    sorted tuples. Everything else falls back to ``repr``, which gives a
    stable, value-oriented representation for numbers, strings, tuples, and
    most user objects. ``None`` and booleans pass through directly.
    """
    if obj is None or isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float, str, bytes)):
        return obj
    if isinstance(obj, (list, tuple)):
        return ("__seq__",) + tuple(_freeze(item) for item in obj)
    if isinstance(obj, dict):
        return ("__map__",) + tuple(
            (_freeze(k), _freeze(v)) for k, v in sorted(obj.items(), key=_sort_key)
        )
    if isinstance(obj, (set, frozenset)):
        return ("__set__",) + tuple(
            _freeze(item) for item in sorted(obj, key=_sort_key)
        )
    return ("__repr__", repr(obj))


def _sort_key(item: Any) -> Any:
    """A sort key that tolerates mixed/unorderable types via ``repr``."""
    try:
        return (0, item)
    except TypeError:
        return (1, repr(item))


def cache(maxsize: int = 128, key: Optional[Callable[..., Any]] = None):
    """Decorator: memoize a function with a bounded LRU cache.

    The wrapped function keeps its name, signature, and call site, so caching
    drops in without changing how the function is used::

        @cachelayer.cache(maxsize=256)
        def expensive(x, y):
            ...

    ``maxsize`` bounds the number of distinct calls remembered; the
    least-recently-used entry is evicted when the cache fills. ``key`` lets
    you supply a custom key function ``(args, kwargs) -> key`` for the rare
    case where the default equality rules aren't what you want.
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        c = Cache(maxsize=maxsize, key=key)

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return c.lookup(c._key(args, kwargs), lambda: func(*args, **kwargs))

        wrapper.cache = c  # type: ignore[attr-defined]
        return wrapper

    return decorator


# A friendlier alias; same behavior, reads well at the call site.
cached = cache