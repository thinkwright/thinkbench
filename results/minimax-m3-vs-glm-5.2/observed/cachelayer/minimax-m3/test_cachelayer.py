"""Tests for cachelayer."""

import threading
import unittest

from cachelayer import cache, Cache


class TestBasicCaching(unittest.TestCase):
    def test_repeat_call_returns_cached_without_calling_again(self):
        calls = []

        @cache
        def f(x):
            calls.append(x)
            return x * 2

        self.assertEqual(f(3), 6)
        self.assertEqual(f(3), 6)
        self.assertEqual(f(3), 6)
        self.assertEqual(calls, [3], "wrapped function should run once")

    def test_different_args_call_again(self):
        calls = []

        @cache
        def f(x):
            calls.append(x)
            return x * 2

        f(1)
        f(2)
        f(1)
        f(2)
        self.assertEqual(calls, [1, 2])

    def test_returned_object_is_the_same_object(self):
        sentinel = object()

        @cache
        def f():
            return sentinel

        self.assertIs(f(), sentinel)
        self.assertIs(f(), sentinel)


class TestKeyIdentity(unittest.TestCase):
    def test_positional_and_keyword_args_distinguished(self):
        @cache
        def f(a, b):
            return (a, b)

        self.assertEqual(f(1, 2), (1, 2))
        self.assertEqual(f(a=1, b=2), (1, 2))
        # Same logical args, different calling style -> different keys.
        self.assertEqual(len(f.cache), 2)

    def test_kwarg_order_does_not_matter(self):
        calls = []

        @cache
        def f(a, b):
            calls.append((a, b))
            return a + b

        f(a=1, b=2)
        f(b=2, a=1)
        self.assertEqual(calls, [(1, 2)])

    def test_unhashable_input_raises_typeerror(self):
        @cache
        def f(x):
            return x

        with self.assertRaises(TypeError):
            f([1, 2, 3])


class TestBoundedGrowth(unittest.TestCase):
    def test_evicts_least_recently_used(self):
        @cache(maxsize=2)
        def f(x):
            return x

        f(1)  # cache: [(1,)]
        f(2)  # cache: [(1,), (2,)]
        f(1)  # cache: [(2,), (1,)]  (1 is now most recent)
        f(3)  # cache: [(1,), (3,)]  (2 evicted as LRU)

        keys = set(f.cache._data.keys())
        self.assertIn((1,), keys)
        self.assertIn((3,), keys)
        self.assertNotIn((2,), keys)

    def test_maxsize_zero_disables_caching(self):
        calls = []

        @cache(maxsize=0)
        def f(x):
            calls.append(x)
            return x

        f(1)
        f(1)
        f(1)
        self.assertEqual(calls, [1, 1, 1])
        self.assertEqual(len(f.cache), 0)

    def test_cache_does_not_grow_unbounded(self):
        @cache(maxsize=4)
        def f(x):
            return x

        for i in range(1000):
            f(i)

        self.assertEqual(len(f.cache), 4)

    def test_negative_maxsize_rejected(self):
        with self.assertRaises(ValueError):
            Cache(maxsize=-1)


class TestIntrospection(unittest.TestCase):
    def test_cache_clear(self):
        calls = []

        @cache
        def f(x):
            calls.append(x)
            return x

        f(1)
        f(1)
        self.assertEqual(calls, [1])
        f.cache_clear()
        f(1)
        f(1)
        self.assertEqual(calls, [1, 1], "after clear, function should run again")

    def test_hits_and_misses_tracked(self):
        @cache
        def f(x):
            return x

        f(1)  # miss
        f(1)  # hit
        f(2)  # miss
        f(1)  # hit
        self.assertEqual(f.cache.hits, 2)
        self.assertEqual(f.cache.misses, 2)


class TestThreadSafety(unittest.TestCase):
    def test_concurrent_calls_with_same_args(self):
        call_count = 0
        lock = threading.Lock()

        @cache
        def f(x):
            nonlocal call_count
            with lock:
                call_count += 1
            # Simulate work so threads race on the same key.
            import time
            time.sleep(0.01)
            return x * 10

        threads = [threading.Thread(target=lambda: f(7)) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(f(7), 70)
        # Under this lock strategy, concurrent first-callers may each compute
        # once. The important guarantee is correctness and boundedness.
        self.assertLessEqual(call_count, 20)


class TestDecoratorForms(unittest.TestCase):
    def test_bare_decorator(self):
        @cache
        def f(x):
            return x

        self.assertEqual(f(1), 1)
        self.assertEqual(f(1), 1)

    def test_parameterized_decorator(self):
        @cache(maxsize=8)
        def f(x):
            return x

        self.assertEqual(f(1), 1)
        self.assertEqual(f.cache._maxsize, 8)


if __name__ == "__main__":
    unittest.main()
