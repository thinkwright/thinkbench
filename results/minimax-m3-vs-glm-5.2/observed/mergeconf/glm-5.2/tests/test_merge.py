"""Tests for mergeconf merge behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mergeconf import mergeconf, MergeResult


# --- basic precedence --------------------------------------------------------

def test_last_source_wins_for_flat_keys():
    result = mergeconf({"a": 1, "b": 1}, {"a": 2}, {"b": 3})
    assert result.config == {"a": 2, "b": 3}


def test_empty_sources_returns_empty():
    result = mergeconf()
    assert result.config == {}
    assert result.provenance == {}


def test_single_source_passes_through():
    result = mergeconf({"x": 1, "y": {"z": 2}})
    assert result.config == {"x": 1, "y": {"z": 2}}


# --- nested / deep merge -----------------------------------------------------

def test_nested_dicts_deep_merge():
    base = {"db": {"host": "localhost", "port": 5432, "pool": {"size": 5}}}
    env = {"db": {"host": "db.internal", "pool": {"timeout": 30}}}
    result = mergeconf(base, env)
    assert result.config == {
        "db": {
            "host": "db.internal",
            "port": 5432,
            "pool": {"size": 5, "timeout": 30},
        }
    }


def test_higher_precedence_dict_replaces_lower_non_dict():
    # base has a scalar, override has a dict -> override wins outright
    result = mergeconf({"feature": True}, {"feature": {"enabled": True}})
    assert result.config == {"feature": {"enabled": True}}


def test_higher_precedence_non_dict_replaces_lower_dict():
    # base has a dict, override has a scalar -> override wins outright
    result = mergeconf({"feature": {"enabled": True}}, {"feature": False})
    assert result.config == {"feature": False}


def test_lists_are_replaced_not_concatenated():
    # Lists are not dicts; the higher-precedence list replaces wholesale.
    result = mergeconf({"hosts": ["a", "b"]}, {"hosts": ["c"]})
    assert result.config == {"hosts": ["c"]}


# --- provenance --------------------------------------------------------------

def test_provenance_records_source_for_each_leaf():
    result = mergeconf(
        {"db": {"host": "localhost", "port": 5432}},
        {"db": {"host": "db.internal"}},
        names=["base", "env"],
    )
    assert result.provenance == {
        "db": {"host": "env", "port": "base"}
    }


def test_provenance_for_dotted_path():
    result = mergeconf(
        {"db": {"pool": {"size": 5}}},
        {"db": {"pool": {"size": 10}}},
        names=["base", "env"],
    )
    assert result.provenance_for("db.pool.size") == "env"
    assert result.provenance_for("db.pool") is None  # not a leaf
    assert result.provenance_for("db.pool.missing") is None


def test_provenance_after_shape_change():
    # When a dict replaces a scalar, provenance is stamped on the new subtree.
    result = mergeconf(
        {"feature": True},
        {"feature": {"enabled": True}},
        names=["base", "env"],
    )
    assert result.provenance == {"feature": {"enabled": "env"}}


# --- isolation / safety ------------------------------------------------------

def test_sources_are_not_mutated():
    base = {"a": {"b": 1}}
    env = {"a": {"c": 2}}
    mergeconf(base, env)
    assert base == {"a": {"b": 1}}
    assert env == {"a": {"c": 2}}


def test_result_does_not_alias_source_dicts():
    base = {"a": {"b": [1, 2]}}
    result = mergeconf(base)
    result.config["a"]["b"].append(3)
    assert base["a"]["b"] == [1, 2]


# --- names / errors ----------------------------------------------------------

def test_names_must_match_source_count():
    with pytest.raises(ValueError):
        mergeconf({"a": 1}, names=["only-one", "two"])


def test_unsupported_source_type_raises():
    with pytest.raises(TypeError):
        mergeconf(123)


# --- file loading ------------------------------------------------------------

def test_merge_from_json_files(tmp_path: Path):
    base = tmp_path / "base.json"
    base.write_text(json.dumps({"db": {"host": "localhost", "port": 5432}}))
    env = tmp_path / "env.json"
    env.write_text(json.dumps({"db": {"host": "db.internal"}}))

    result = mergeconf(base, env)
    assert result.config == {
        "db": {"host": "db.internal", "port": 5432}
    }
    assert result.provenance_for("db.host") == "env.json"
    assert result.provenance_for("db.port") == "base.json"


def test_merge_mixed_dict_and_file(tmp_path: Path):
    base = tmp_path / "base.json"
    base.write_text(json.dumps({"a": 1, "b": 1}))
    result = mergeconf(base, {"b": 2, "c": 3}, names=[None, "runtime"])
    assert result.config == {"a": 1, "b": 2, "c": 3}
    assert result.provenance_for("a") == "base.json"
    assert result.provenance_for("b") == "runtime"
    assert result.provenance_for("c") == "runtime"


def test_non_mapping_top_level_file_raises(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(ValueError):
        mergeconf(bad)