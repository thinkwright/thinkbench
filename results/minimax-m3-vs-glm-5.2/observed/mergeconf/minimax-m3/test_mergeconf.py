"""Tests for mergeconf. Run with: python -m pytest test_mergeconf.py -v
"""

import json
import subprocess
import sys

import pytest

import mergeconf


# ---------------------------------------------------------------------------
# merge() — core behavior
# ---------------------------------------------------------------------------

def test_empty_sources():
    eff, prov = mergeconf.merge([])
    assert eff == {}
    assert prov == {}


def test_single_source():
    eff, prov = mergeconf.merge([("a", {"x": 1, "y": "hello"})])
    assert eff == {"x": 1, "y": "hello"}
    assert prov == {"x": {"source": "a", "value": 1},
                    "y": {"source": "a", "value": "hello"}}


def test_later_source_overrides_earlier():
    eff, prov = mergeconf.merge([
        ("defaults", {"port": 8080, "host": "localhost"}),
        ("env",      {"port": 9090}),
    ])
    assert eff == {"port": 9090, "host": "localhost"}
    assert prov["port"]["source"] == "env"
    assert prov["port"]["value"] == 9090
    assert prov["host"]["source"] == "defaults"


def test_deep_merge_does_not_clobber_siblings():
    """The whole point of nested merge: override one leaf, keep the rest."""
    eff, prov = mergeconf.merge([
        ("defaults", {"db": {"host": "localhost", "port": 5432, "user": "app"}}),
        ("env",      {"db": {"host": "db.prod.internal"}}),
    ])
    assert eff == {"db": {"host": "db.prod.internal", "port": 5432, "user": "app"}}
    assert prov["db.host"]["source"] == "env"
    assert prov["db.port"]["source"] == "defaults"
    assert prov["db.user"]["source"] == "defaults"


def test_new_keys_in_higher_source_are_added():
    eff, _ = mergeconf.merge([
        ("defaults", {"a": 1}),
        ("env",      {"b": 2}),
    ])
    assert eff == {"a": 1, "b": 2}


def test_lists_are_replaced_not_merged():
    """Lists are values, not maps. We replace them wholesale — this is
    the only behavior with a defensible default; element-wise list
    merging is a policy decision the caller should make explicitly."""
    eff, prov = mergeconf.merge([
        ("defaults", {"allowed": ["a", "b", "c"]}),
        ("env",      {"allowed": ["x", "y"]}),
    ])
    assert eff == {"allowed": ["x", "y"]}
    assert prov["allowed"]["source"] == "env"


def test_type_conflict_higher_source_wins_and_is_recorded():
    """If one source says dict and another says string, the higher wins
    and provenance records what was replaced."""
    eff, prov = mergeconf.merge([
        ("defaults", {"feature": {"enabled": True, "ratio": 0.5}}),
        ("env",      {"feature": "off"}),
    ])
    assert eff == {"feature": "off"}
    assert prov["feature"]["source"] == "env"
    assert prov["feature"]["value"] == "off"
    assert prov["feature"]["replaced"]["value"] == {"enabled": True, "ratio": 0.5}


def test_same_value_no_replaced_field():
    """If both sources agree, no 'replaced' noise in provenance."""
    eff, prov = mergeconf.merge([
        ("defaults", {"x": 1}),
        ("env",      {"x": 1}),
    ])
    assert "replaced" not in prov["x"]
    assert prov["x"]["source"] == "env"


def test_three_layer_merge():
    eff, prov = mergeconf.merge([
        ("defaults", {"a": 1, "b": {"c": 2, "d": 3}}),
        ("env",      {"b": {"c": 20}}),
        ("cli",      {"a": 100, "b": {"d": 30}}),
    ])
    assert eff == {"a": 100, "b": {"c": 20, "d": 30}}
    assert prov["a"]["source"] == "cli"
    assert prov["b.c"]["source"] == "env"
    assert prov["b.d"]["source"] == "cli"


def test_non_dict_source_raises():
    with pytest.raises(ValueError, match="mapping"):
        mergeconf.merge([("bad", [1, 2, 3])])


def test_provenance_paths_are_dotted():
    eff, prov = mergeconf.merge([
        ("a", {"x": {"y": {"z": 1}}}),
    ])
    assert "x.y.z" in prov
    assert prov["x.y.z"]["value"] == 1


# ---------------------------------------------------------------------------
# load()
# ---------------------------------------------------------------------------

def test_load_json(tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text('{"port": 8080, "debug": true}')
    assert mergeconf.load(str(p)) == {"port": 8080, "debug": True}


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        mergeconf.load(str(tmp_path / "nope.json"))


def test_load_non_mapping_top_level_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('[1, 2, 3]')
    with pytest.raises(ValueError, match="mapping"):
        mergeconf.load(str(p))


def test_load_empty_file_returns_empty_dict(tmp_path):
    p = tmp_path / "empty.json"
    p.write_text("")
    assert mergeconf.load(str(p)) == {}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _run_cli(*args):
    return subprocess.run(
        [sys.executable, "mergeconf.py", *args],
        capture_output=True, text=True, check=False,
    )


def test_cli_no_args_prints_empty_object():
    r = _run_cli()
    assert r.returncode == 0
    assert json.loads(r.stdout) == {}


def test_cli_merges_files(tmp_path):
    a = tmp_path / "defaults.json"
    b = tmp_path / "env.json"
    a.write_text('{"port": 8080, "host": "localhost"}')
    b.write_text('{"port": 9090}')
    r = _run_cli(str(a), str(b))
    assert r.returncode == 0
    assert json.loads(r.stdout) == {"port": 9090, "host": "localhost"}


def test_cli_override_scalar(tmp_path):
    a = tmp_path / "defaults.json"
    a.write_text('{"port": 8080}')
    r = _run_cli(str(a), "--override", "port=9090")
    assert r.returncode == 0
    assert json.loads(r.stdout) == {"port": 9090}


def test_cli_override_parses_json_values(tmp_path):
    a = tmp_path / "defaults.json"
    a.write_text('{}')
    r = _run_cli(str(a),
                 "--override", "debug=true",
                 "--override", "ratio=0.25",
                 "--override", 'tags=["a","b"]')
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out == {"debug": True, "ratio": 0.25, "tags": ["a", "b"]}


def test_cli_override_dotted_key(tmp_path):
    a = tmp_path / "defaults.json"
    a.write_text('{"db": {"host": "localhost", "port": 5432}}')
    r = _run_cli(str(a), "--override", "db.host=db.prod")
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out == {"db": {"host": "db.prod", "port": 5432}}


def test_cli_explain_includes_provenance(tmp_path):
    a = tmp_path / "defaults.json"
    a.write_text('{"port": 8080}')
    r = _run_cli(str(a), "--override", "port=9090", "--explain")
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["config"] == {"port": 9090}
    assert out["provenance"]["port"]["source"] == "cli"
    assert out["provenance"]["port"]["value"] == 9090


def test_cli_bad_override_format(tmp_path):
    a = tmp_path / "defaults.json"
    a.write_text('{}')
    r = _run_cli(str(a), "--override", "noequalssign")
    assert r.returncode != 0
    assert "key.path=value" in r.stderr


def test_cli_missing_file_errors(tmp_path):
    r = _run_cli(str(tmp_path / "nope.json"))
    assert r.returncode != 0


# ---------------------------------------------------------------------------
# End-to-end: the "why is this value what it is" question
# ---------------------------------------------------------------------------

def test_audit_trail_answers_why_question(tmp_path):
    """The motivating use case: an operator asks why a value is what it is,
    and we can point at the exact source."""
    defaults = tmp_path / "defaults.json"
    env = tmp_path / "env.json"
    defaults.write_text(json.dumps({
        "server": {"host": "0.0.0.0", "port": 8080, "workers": 4},
        "logging": {"level": "INFO"},
    }))
    env.write_text(json.dumps({
        "server": {"port": 9000},
        "logging": {"level": "DEBUG"},
    }))

    r = _run_cli(str(defaults), str(env), "--explain")
    assert r.returncode == 0
    out = json.loads(r.stdout)

    # server.host was untouched -> defaults
    assert out["provenance"]["server.host"]["source"].endswith("defaults.json")
    # server.port was overridden -> env
    assert out["provenance"]["server.port"]["source"].endswith("env.json")
    # logging.level was overridden -> env
    assert out["provenance"]["logging.level"]["source"].endswith("env.json")
