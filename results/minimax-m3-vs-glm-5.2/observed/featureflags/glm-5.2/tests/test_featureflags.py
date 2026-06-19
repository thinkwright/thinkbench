"""Tests for flag evaluation behavior."""

import json
import os
import tempfile
import unittest

from featureflags import FlagClient, Flag, State


def client(*flags: Flag) -> FlagClient:
    return FlagClient.with_flags(flags)


class FlatOnOffTests(unittest.TestCase):
    def test_on_is_always_on(self):
        f = Flag("x", State.ON)
        self.assertTrue(f.is_enabled())
        self.assertTrue(f.is_enabled({"user_id": "anyone"}))

    def test_off_is_always_off(self):
        f = Flag("x", State.OFF)
        self.assertFalse(f.is_enabled())
        self.assertFalse(f.is_enabled({"user_id": "anyone"}))

    def test_client_on_off(self):
        c = client(Flag("on-flag", State.ON), Flag("off-flag", State.OFF))
        self.assertTrue(c.is_enabled("on-flag"))
        self.assertFalse(c.is_enabled("off-flag"))


class UndefinedFlagTests(unittest.TestCase):
    def test_undefined_defaults_to_false(self):
        c = client()
        self.assertFalse(c.is_enabled("nope"))

    def test_undefined_respects_default(self):
        c = client()
        self.assertTrue(c.is_enabled("nope", default=True))

    def test_undefined_does_not_raise(self):
        c = client()
        # No context, no flag, no problem.
        self.assertFalse(c.is_enabled("nope", context=None))

    def test_contains(self):
        c = client(Flag("x", State.ON))
        self.assertIn("x", c)
        self.assertNotIn("y", c)


class UserIdsTests(unittest.TestCase):
    def setUp(self):
        self.flag = Flag(
            "team-only",
            State.ROLLOUT,
            rules={"user_ids": ["alice", "bob"]},
        )
        self.c = client(self.flag)

    def test_listed_user_is_on(self):
        self.assertTrue(self.c.is_enabled("team-only", {"user_id": "alice"}))
        self.assertTrue(self.c.is_enabled("team-only", {"user_id": "bob"}))

    def test_unlisted_user_is_off(self):
        self.assertFalse(self.c.is_enabled("team-only", {"user_id": "carol"}))

    def test_no_user_is_off(self):
        self.assertFalse(self.c.is_enabled("team-only", {}))
        self.assertFalse(self.c.is_enabled("team-only", None))


class PercentageTests(unittest.TestCase):
    def test_zero_is_off(self):
        f = Flag("p", State.ROLLOUT, rules={"percentage": 0})
        self.assertFalse(f.is_enabled({"user_id": "alice"}))

    def test_hundred_is_on(self):
        f = Flag("p", State.ROLLOUT, rules={"percentage": 100})
        self.assertTrue(f.is_enabled({"user_id": "alice"}))

    def test_no_user_is_off(self):
        f = Flag("p", State.ROLLOUT, rules={"percentage": 50})
        self.assertFalse(f.is_enabled({}))

    def test_is_deterministic(self):
        f = Flag("p", State.ROLLOUT, rules={"percentage": 25})
        # Same user always gets the same answer.
        results = {f.is_enabled({"user_id": "alice"}) for _ in range(20)}
        self.assertEqual(len(results), 1)

    def test_distribution_is_roughly_right(self):
        f = Flag("p", State.ROLLOUT, rules={"percentage": 30})
        users = [f"user-{i}" for i in range(1000)]
        on = sum(1 for u in users if f.is_enabled({"user_id": u}))
        # Expect ~300; allow a generous band since it's a hash bucket.
        self.assertGreater(on, 200)
        self.assertLess(on, 400)

    def test_50_50_splits_users(self):
        f = Flag("p", State.ROLLOUT, rules={"percentage": 50})
        users = [f"user-{i}" for i in range(1000)]
        on = sum(1 for u in users if f.is_enabled({"user_id": u}))
        self.assertGreater(on, 400)
        self.assertLess(on, 600)


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.flag = Flag(
            "staging-only",
            State.ROLLOUT,
            rules={"environments": ["staging", "dev"]},
        )
        self.c = client(self.flag)

    def test_matching_env_on(self):
        self.assertTrue(self.c.is_enabled("staging-only", {"env": "staging"}))
        self.assertTrue(self.c.is_enabled("staging-only", {"env": "dev"}))

    def test_non_matching_env_off(self):
        self.assertFalse(self.c.is_enabled("staging-only", {"env": "prod"}))

    def test_no_env_off(self):
        self.assertFalse(self.c.is_enabled("staging-only", {}))


class CombinedRulesTests(unittest.TestCase):
    """Rules combine with AND: every present rule must match."""

    def setUp(self):
        self.flag = Flag(
            "combined",
            State.ROLLOUT,
            rules={
                "user_ids": ["alice", "bob"],
                "environments": ["staging"],
            },
        )
        self.c = client(self.flag)

    def test_both_match(self):
        self.assertTrue(
            self.c.is_enabled("combined", {"user_id": "alice", "env": "staging"})
        )

    def test_user_match_env_mismatch(self):
        self.assertFalse(
            self.c.is_enabled("combined", {"user_id": "alice", "env": "prod"})
        )

    def test_env_match_user_mismatch(self):
        self.assertFalse(
            self.c.is_enabled("combined", {"user_id": "carol", "env": "staging"})
        )

    def test_percentage_and_env(self):
        flag = Flag(
            "p-env",
            State.ROLLOUT,
            rules={"percentage": 100, "environments": ["staging"]},
        )
        c = client(flag)
        self.assertTrue(c.is_enabled("p-env", {"user_id": "a", "env": "staging"}))
        self.assertFalse(c.is_enabled("p-env", {"user_id": "a", "env": "prod"}))


class EmptyRolloutTests(unittest.TestCase):
    def test_rollout_with_no_rules_is_off(self):
        f = Flag("r", State.ROLLOUT, rules={})
        self.assertFalse(f.is_enabled({"user_id": "a"}))

    def test_unknown_state_fails_closed(self):
        f = Flag("weird", state="bogus")
        self.assertFalse(f.is_enabled({"user_id": "a"}))


class FileLoadingTests(unittest.TestCase):
    def _write(self, data: dict) -> str:
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path

    def test_load_from_file(self):
        path = self._write(
            {
                "flags": [
                    {"name": "a", "state": "on"},
                    {"name": "b", "state": "off"},
                    {
                        "name": "c",
                        "state": "rollout",
                        "rules": {"user_ids": ["alice"]},
                    },
                ]
            }
        )
        c = FlagClient.from_file(path)
        self.assertTrue(c.is_enabled("a"))
        self.assertFalse(c.is_enabled("b"))
        self.assertTrue(c.is_enabled("c", {"user_id": "alice"}))
        self.assertFalse(c.is_enabled("c", {"user_id": "bob"}))
        os.unlink(path)

    def test_empty_file(self):
        path = self._write({"flags": []})
        c = FlagClient.from_file(path)
        self.assertFalse(c.is_enabled("anything"))
        os.unlink(path)


class InspectionTests(unittest.TestCase):
    def test_summary_lists_all_flags_sorted(self):
        c = client(
            Flag("b", State.ON),
            Flag("a", State.OFF),
            Flag("c", State.ROLLOUT, rules={"percentage": 10}),
        )
        summary = c.summary()
        self.assertEqual([s["name"] for s in summary], ["a", "b", "c"])
        self.assertEqual(summary[2]["rules"], {"percentage": 10})

    def test_all_sorted(self):
        c = client(Flag("b", State.ON), Flag("a", State.OFF))
        self.assertEqual([f.name for f in c.all()], ["a", "b"])

    def test_get_returns_flag_or_none(self):
        c = client(Flag("x", State.ON))
        self.assertEqual(c.get("x").state, State.ON)
        self.assertIsNone(c.get("missing"))


if __name__ == "__main__":
    unittest.main()