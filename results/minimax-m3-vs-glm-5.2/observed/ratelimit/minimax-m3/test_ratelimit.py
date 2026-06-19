"""Tests for ratelimit. Run with: python -m unittest test_ratelimit"""

from __future__ import annotations

import threading
import unittest
from ratelimit import Decision, Limiter


class FakeClock:
    """A monotonic clock the tests can advance by hand."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class TokenBucketBasics(unittest.TestCase):
    def test_allows_up_to_capacity_then_denies(self):
        clock = FakeClock()
        lim = Limiter(capacity=5, rate=1.0, clock=clock)

        decisions = [lim.check("alice") for _ in range(5)]
        self.assertTrue(all(d.allowed for d in decisions),
                        "first 5 should all be allowed")
        self.assertEqual(decisions[-1].remaining, 0.0)

        d = lim.check("alice")
        self.assertFalse(d.allowed)
        self.assertGreater(d.retry_after, 0.0)

    def test_steady_state_matches_rate(self):
        # 2 tokens/sec, capacity 2. After draining, we should get one
        # allowed check per 0.5s of wall time.
        clock = FakeClock()
        lim = Limiter(capacity=2, rate=2.0, clock=clock)

        # Drain the bucket.
        self.assertTrue(lim.check("alice").allowed)
        self.assertTrue(lim.check("alice").allowed)
        self.assertFalse(lim.check("alice").allowed)

        # After 0.5s, exactly one token should have accrued.
        clock.advance(0.5)
        self.assertTrue(lim.check("alice").allowed)
        self.assertFalse(lim.check("alice").allowed)

        # After another 0.5s, another one.
        clock.advance(0.5)
        self.assertTrue(lim.check("alice").allowed)

    def test_burst_is_exactly_capacity_not_more(self):
        # The promise of a token bucket: the burst limit is the burst limit.
        clock = FakeClock()
        lim = Limiter(capacity=7, rate=100.0, clock=clock)

        allowed = sum(1 for _ in range(20) if lim.check("k").allowed)
        self.assertEqual(allowed, 7)

    def test_refill_caps_at_capacity(self):
        # A bucket that has been idle for a long time should not exceed
        # capacity. Otherwise the "limit" stops meaning anything.
        clock = FakeClock()
        lim = Limiter(capacity=3, rate=10.0, clock=clock)

        clock.advance(1000.0)  # a very long time
        allowed = sum(1 for _ in range(10) if lim.check("k").allowed)
        self.assertEqual(allowed, 3)


class Isolation(unittest.TestCase):
    def test_keys_do_not_interfere(self):
        clock = FakeClock()
        lim = Limiter(capacity=2, rate=0.001, clock=clock)

        # Drain alice.
        self.assertTrue(lim.check("alice").allowed)
        self.assertTrue(lim.check("alice").allowed)
        self.assertFalse(lim.check("alice").allowed)

        # Bob should still have a full bucket.
        self.assertTrue(lim.check("bob").allowed)
        self.assertTrue(lim.check("bob").allowed)
        self.assertFalse(lim.check("bob").allowed)

        # And alice is still empty.
        self.assertFalse(lim.check("alice").allowed)

    def test_new_key_starts_full(self):
        clock = FakeClock()
        lim = Limiter(capacity=4, rate=1.0, clock=clock)

        for _ in range(4):
            self.assertTrue(lim.check("fresh").allowed)
        self.assertFalse(lim.check("fresh").allowed)


class DecisionShape(unittest.TestCase):
    def test_decision_carries_explanation(self):
        clock = FakeClock()
        lim = Limiter(capacity=3, rate=2.0, clock=clock)

        d_allow = lim.check("k")
        self.assertIsInstance(d_allow, Decision)
        self.assertTrue(d_allow.allowed)
        self.assertEqual(d_allow.key, "k")
        self.assertEqual(d_allow.limit, 3)
        self.assertEqual(d_allow.rate, 2.0)
        self.assertEqual(d_allow.retry_after, 0.0)
        self.assertAlmostEqual(d_allow.remaining, 2.0)

        # Drain.
        lim.check("k")
        lim.check("k")
        d_deny = lim.check("k")
        self.assertFalse(d_deny.allowed)
        # 1 token needed, 0 in bucket, 2 tokens/sec -> 0.5s
        self.assertAlmostEqual(d_deny.retry_after, 0.5, places=6)
        self.assertEqual(d_deny.remaining, 0.0)

    def test_repr_is_readable(self):
        clock = FakeClock()
        lim = Limiter(capacity=2, rate=1.0, clock=clock)
        # Drain the bucket so the third check is denied.
        lim.check("k")
        lim.check("k")
        d = lim.check("k")
        s = repr(d)
        self.assertIn("deny", s)
        self.assertIn("k", s)
        self.assertIn("retry_after", s)


class CostParameter(unittest.TestCase):
    def test_cost_greater_than_one(self):
        clock = FakeClock()
        lim = Limiter(capacity=5, rate=1.0, clock=clock)
        # A request that costs 3 tokens.
        self.assertTrue(lim.check("k", cost=3).allowed)
        # Two more single-token requests should fit (2 left).
        self.assertTrue(lim.check("k").allowed)
        self.assertTrue(lim.check("k").allowed)
        # Bucket empty now.
        self.assertFalse(lim.check("k").allowed)

    def test_cost_exactly_capacity(self):
        clock = FakeClock()
        lim = Limiter(capacity=4, rate=1.0, clock=clock)
        self.assertTrue(lim.check("k", cost=4).allowed)
        self.assertFalse(lim.check("k").allowed)


class Reset(unittest.TestCase):
    def test_reset_one_key(self):
        clock = FakeClock()
        lim = Limiter(capacity=2, rate=0.001, clock=clock)
        lim.check("alice")
        lim.check("alice")
        self.assertFalse(lim.check("alice").allowed)

        lim.reset("alice")
        self.assertTrue(lim.check("alice").allowed)
        self.assertTrue(lim.check("alice").allowed)

    def test_reset_all(self):
        clock = FakeClock()
        lim = Limiter(capacity=1, rate=0.001, clock=clock)
        lim.check("a")
        lim.check("b")
        lim.reset()
        self.assertTrue(lim.check("a").allowed)
        self.assertTrue(lim.check("b").allowed)


class ConstructionValidation(unittest.TestCase):
    def test_rejects_zero_or_negative_capacity(self):
        with self.assertRaises(ValueError):
            Limiter(capacity=0, rate=1.0)

    def test_rejects_zero_or_negative_rate(self):
        with self.assertRaises(ValueError):
            Limiter(capacity=1, rate=0)

    def test_rejects_zero_or_negative_cost(self):
        clock = FakeClock()
        lim = Limiter(capacity=1, rate=1.0, clock=clock)
        with self.assertRaises(ValueError):
            lim.check("k", cost=0)


class Concurrency(unittest.TestCase):
    def test_total_allowed_equals_capacity_under_threads(self):
        # Hammer the limiter from many threads and confirm the total
        # number of allowed checks is exactly the capacity. This is the
        # property that matters in practice: under a burst, the limit
        # you promise is the limit you get.
        clock = FakeClock()
        lim = Limiter(capacity=50, rate=1000.0, clock=clock)

        allowed = []
        lock = threading.Lock()

        def worker():
            d = lim.check("shared")
            if d.allowed:
                with lock:
                    allowed.append(1)

        threads = [threading.Thread(target=worker) for _ in range(200)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sum(allowed), 50)

    def test_keys_isolated_under_threads(self):
        clock = FakeClock()
        lim = Limiter(capacity=3, rate=0.001, clock=clock)

        results: dict[str, int] = {}
        lock = threading.Lock()

        def worker(key: str):
            d = lim.check(key)
            if d.allowed:
                with lock:
                    results[key] = results.get(key, 0) + 1

        threads = []
        for k in ("a", "b", "c"):
            for _ in range(10):
                threads.append(threading.Thread(target=worker, args=(k,)))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Each key should have been allowed exactly its capacity.
        self.assertEqual(results, {"a": 3, "b": 3, "c": 3})


if __name__ == "__main__":
    unittest.main()
