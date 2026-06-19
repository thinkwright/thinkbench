"""Tests for featureflags evaluation behaviour."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import featureflags as ff


@pytest.fixture
def flags_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point FEATUREFLAGS_PATH at a tmp file and reload the registry."""
    path = tmp_path / "flags.json"
    monkeypatch.setenv("FEATUREFLAGS_PATH", str(path))
    ff.reload()
    yield path
    ff.reload()


def write_flags(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")
    ff.reload()


# --- unknown flags ---------------------------------------------------------

def test_unknown_flag_returns_false(flags_file: Path) -> None:
    write_flags(flags_file, {})
    assert ff.is_enabled("does_not_exist") is False


def test_unknown_flag_does_not_raise(flags_file: Path) -> None:
    write_flags(flags_file, {})
    # Should not raise even with arbitrary context.
    assert ff.is_enabled("nope", user_id="u1", env="prod") is False


# --- master switch ---------------------------------------------------------

def test_disabled_flag_is_off(flags_file: Path) -> None:
    write_flags(flags_file, {"x": {"enabled": False}})
    assert ff.is_enabled("x", user_id="u1") is False


def test_enabled_flag_with_no_rollout_is_on(flags_file: Path) -> None:
    write_flags(flags_file, {"x": {"enabled": True}})
    assert ff.is_enabled("x", user_id="u1") is True


def test_missing_enabled_defaults_to_off(flags_file: Path) -> None:
    write_flags(flags_file, {"x": {}})
    assert ff.is_enabled("x", user_id="u1") is False


# --- environment scoping ---------------------------------------------------

def test_env_scoping_on_match(flags_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATUREFLAGS_ENV", "prod")
    write_flags(flags_file, {"x": {"enabled": True, "environments": ["prod", "staging"]}})
    assert ff.is_enabled("x", user_id="u1") is True


def test_env_scoping_off_mismatch(flags_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATUREFLAGS_ENV", "dev")
    write_flags(flags_file, {"x": {"enabled": True, "environments": ["prod"]}})
    assert ff.is_enabled("x", user_id="u1") is False


def test_env_scoping_context_overrides_env_var(
    flags_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FEATUREFLAGS_ENV", "dev")
    write_flags(flags_file, {"x": {"enabled": True, "environments": ["prod"]}})
    assert ff.is_enabled("x", user_id="u1", env="prod") is True


def test_env_scoping_no_env_anywhere_is_off(
    flags_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ensure no env var leaks in from the test runner.
    monkeypatch.setenv("FEATUREFLAGS_ENV", "")
    write_flags(flags_file, {"x": {"enabled": True, "environments": ["prod"]}})
    assert ff.is_enabled("x", user_id="u1") is False


# --- percentage rollout ----------------------------------------------------

def test_rollout_zero_is_off(flags_file: Path) -> None:
    write_flags(flags_file, {"x": {"enabled": True, "rollout": {"percent": 0}}})
    assert ff.is_enabled("x", user_id="u1") is False


def test_rollout_hundred_is_on(flags_file: Path) -> None:
    write_flags(flags_file, {"x": {"enabled": True, "rollout": {"percent": 100}}})
    assert ff.is_enabled("x", user_id="u1") is True


def test_rollout_stable_per_subject(flags_file: Path) -> None:
    write_flags(flags_file, {"x": {"enabled": True, "rollout": {"percent": 50}}})
    # Same subject, same answer, every time.
    for _ in range(20):
        a = ff.is_enabled("x", user_id="user-42")
        b = ff.is_enabled("x", user_id="user-42")
        assert a == b


def test_rollout_distribution_is_reasonable(flags_file: Path) -> None:
    """At 50%, roughly half of a large population should be on."""
    write_flags(flags_file, {"x": {"enabled": True, "rollout": {"percent": 50}}})
    on = sum(1 for i in range(2000) if ff.is_enabled("x", user_id=f"u{i}"))
    # Generous bounds to avoid flakiness; SHA-256 distribution is tight.
    assert 850 <= on <= 1150, f"expected ~1000 on, got {on}"


def test_rollout_different_flags_different_buckets(flags_file: Path) -> None:
    """The same subject should be able to be on for one flag and off for another."""
    write_flags(
        flags_file,
        {
            "a": {"enabled": True, "rollout": {"percent": 50}},
            "b": {"enabled": True, "rollout": {"percent": 50}},
        },
    )
    # Across many subjects, the two flags should not be perfectly correlated.
    same = 0
    total = 0
    for i in range(500):
        ua = ff.is_enabled("a", user_id=f"u{i}")
        ub = ff.is_enabled("b", user_id=f"u{i}")
        if ua == ub:
            same += 1
        total += 1
    # If perfectly correlated, same == total. Should be well under that.
    assert same < total * 0.7, f"flags appear correlated: {same}/{total} same"


def test_rollout_no_subject_is_off(flags_file: Path) -> None:
    # Partial rollout with no subject: can't decide stably, so be conservative.
    write_flags(flags_file, {"x": {"enabled": True, "rollout": {"percent": 50}}})
    assert ff.is_enabled("x") is False


def test_rollout_custom_key(flags_file: Path) -> None:
    write_flags(
        flags_file,
        {"x": {"enabled": True, "rollout": {"percent": 50, "key": "account_id"}}},
    )
    # With the configured key present, the rollout decides.
    a = ff.is_enabled("x", account_id="acct-1")
    b = ff.is_enabled("x", account_id="acct-1")
    assert a == b  # stable
    # user_id is ignored when key is account_id; absence of account_id → off.
    assert ff.is_enabled("x", user_id="u1") is False


# --- combined: env + rollout ----------------------------------------------

def test_env_and_rollout_both_must_pass(
    flags_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FEATUREFLAGS_ENV", "prod")
    write_flags(
        flags_file,
        {
            "x": {
                "enabled": True,
                "environments": ["prod"],
                "rollout": {"percent": 100},
            }
        },
    )
    assert ff.is_enabled("x", user_id="u1") is True

    monkeypatch.setenv("FEATUREFLAGS_ENV", "dev")
    assert ff.is_enabled("x", user_id="u1") is False


# --- introspection --------------------------------------------------------

def test_snapshot_lists_all_flags(flags_file: Path) -> None:
    write_flags(
        flags_file,
        {
            "alpha": {"enabled": True},
            "beta": {"enabled": False, "environments": ["prod"]},
        },
    )
    snap = ff.registry().snapshot()
    assert set(snap.keys()) == {"alpha", "beta"}
    assert snap["alpha"]["enabled"] is True
    assert snap["beta"]["environments"] == ["prod"]


def test_registry_names_sorted(flags_file: Path) -> None:
    write_flags(
        flags_file,
        {"zeta": {"enabled": True}, "alpha": {"enabled": True}, "mu": {"enabled": True}},
    )
    assert ff.registry().names() == ["alpha", "mu", "zeta"]


# --- reload & resilience ---------------------------------------------------

def test_reload_picks_up_changes(flags_file: Path) -> None:
    write_flags(flags_file, {"x": {"enabled": False}})
    assert ff.is_enabled("x", user_id="u1") is False
    write_flags(flags_file, {"x": {"enabled": True}})
    assert ff.is_enabled("x", user_id="u1") is True


def test_missing_file_means_no_flags(flags_file: Path) -> None:
    # Fixture points at a path that has never been written to.
    assert not flags_file.exists()
    ff.reload()
    assert ff.is_enabled("anything") is False
    assert ff.registry().snapshot() == {}


def test_corrupt_file_does_not_crash(flags_file: Path) -> None:
    flags_file.write_text("{not valid json", encoding="utf-8")
    ff.reload()
    assert ff.is_enabled("anything") is False


def test_non_object_root_does_not_crash(flags_file: Path) -> None:
    flags_file.write_text("[1, 2, 3]", encoding="utf-8")
    ff.reload()
    assert ff.is_enabled("anything") is False


def test_malformed_entry_is_skipped(flags_file: Path) -> None:
    flags_file.write_text(
        json.dumps({"good": {"enabled": True}, "bad": "not a dict"}),
        encoding="utf-8",
    )
    ff.reload()
    assert ff.is_enabled("good", user_id="u1") is True
    assert ff.is_enabled("bad", user_id="u1") is False
