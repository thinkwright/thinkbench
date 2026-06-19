"""Tests for the behavior that matters in cachelayer."""

import cachelayer


class Counter:
    """A callable that records how many times it actually ran."""

    def __init__(self, func):
        self.func = func
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self.func(*args, **kwargs)


# --- hits come back without redoing the work -------------------------------


def test_hit_returns_cached_value_and_skips_work():
    seen = Counter(lambda x: x * 2)

    @cachelayer.cache()
    def double(x):
        return seen(x)

    assert double(3) == 6
    assert double(3) == 6  # repeat call, same args
    assert seen.calls == 1, "the second call should have been a cache hit"


def test_distinct_args_do_real_work():
    seen = Counter(lambda x: x + 1)

    @cachelayer.cache()
    def inc(x):
        return seen(x)

    inc(1)
    inc(2)
    inc(1)
    assert seen.calls == 2, "only the two distinct inputs should run"


def test_keyword_order_does_not_matter():
    seen = Counter(lambda a, b: a + b)

    @cachelayer.cache()
    def add(a, b):
        return seen(a, b)

    assert add(a=1, b=2) == 3
    assert add(b=2, a=1) == 3
    assert seen.calls == 1, "f(a=1, b=2) and f(b=2, a=1) are the same call"


def test_unhashable_args_compared_by_value():
    seen = Counter(lambda spec: sum(spec["values"]))

    @cachelayer.cache()
    def total(spec):
        return seen(spec)

    assert total({"values": [1, 2, 3]}) == 6
    # A fresh, equal dict/list — different identity, same content.
    assert total({"values": [1, 2, 3]}) == 6
    assert seen.calls == 1, "value-equal unhashable args should hit"


def test_different_unhashable_values_miss():
    seen = Counter(lambda spec: sum(spec["values"]))

    @cachelayer.cache()
    def total(spec):
        return seen(spec)

    total({"values": [1, 2, 3]})
    total({"values": [1, 2, 4]})
    assert seen.calls == 2


# --- the cache does the right thing as it fills up ------------------------


def test_lru_evicts_least_recently_used():
    seen = Counter(lambda x: x)

    @cachelayer.cache(maxsize=2)
    def f(x):
        return seen(x)

    f(1)  # cache: [1]
    f(2)  # cache: [1, 2]
    f(1)  # hit -> 1 becomes most recent; cache: [2, 1]
    f(3)  # full -> evict LRU (2); cache: [1, 3]
    f(2)  # miss -> 2 must run again
    assert seen.calls == 4, "2 should have been evicted and recomputed"


def test_maxsize_bounds_entries():
    @cachelayer.cache(maxsize=3)
    def f(x):
        return x

    for i in range(10):
        f(i)
    assert f.cache.size == 3


def test_repeated_calls_within_bounds_all_hit():
    seen = Counter(lambda x: x)

    @cachelayer.cache(maxsize=5)
    def f(x):
        return seen(x)

    for _ in range(100):
        f(1)
    assert seen.calls == 1
    assert f.cache.size == 1


def test_rejects_nonpositive_maxsize():
    import pytest

    with pytest.raises(ValueError):
        cachelayer.cache(maxsize=0)(lambda x: x)


# --- trustworthiness: the cache stays in your control ---------------------


def test_clear_drops_everything():
    seen = Counter(lambda x: x)

    @cachelayer.cache()
    def f(x):
        return seen(x)

    f(1)
    f(1)
    assert seen.calls == 1
    f.cache.clear()
    assert f.cache.size == 0
    f(1)
    assert seen.calls == 2, "after clear the work must be redone"


def test_evict_removes_single_entry():
    seen = Counter(lambda x: x)

    @cachelayer.cache()
    def f(x):
        return seen(x)

    f(1)
    f.cache.evict(f.cache._key((1,), {}))
    f(1)
    assert seen.calls == 2


def test_stats_track_hits_and_misses():
    @cachelayer.cache()
    def f(x):
        return x

    f(1)  # miss
    f(1)  # hit
    f(2)  # miss
    assert f.cache.hits == 1
    assert f.cache.misses == 2


# --- direct Cache usage ----------------------------------------------------


def test_cache_object_can_be_used_directly():
    c = cachelayer.Cache(maxsize=4)
    expensive = Counter(lambda x: x * 10)

    assert c.call(expensive, 5) == 50
    assert c.call(expensive, 5) == 50
    assert expensive.calls == 1
    assert c.hits == 1
    assert c.misses == 1


def test_preserves_function_metadata():
    @cachelayer.cache()
    def meaningful_name(x):
        """A docstring worth keeping."""
        return x

    assert meaningful_name.__name__ == "meaningful_name"
    assert meaningful_name.__doc__ == "A docstring worth keeping."


def test_cached_alias_matches_cache():
    @cachelayer.cached(maxsize=2)
    def f(x):
        return x

    assert f(1) == 1
    assert isinstance(f.cache, cachelayer.Cache)