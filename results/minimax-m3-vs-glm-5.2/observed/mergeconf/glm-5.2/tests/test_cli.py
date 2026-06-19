"""Tests for the CLI entry point (python -m mergeconf)."""

from __future__ import annotations

import json
from pathlib import Path

from mergeconf.__main__ import main


def test_cli_prints_merged_config(tmp_path: Path, capsys):
    base = tmp_path / "base.json"
    base.write_text(json.dumps({"db": {"host": "localhost", "port": 5432}}))
    env = tmp_path / "env.json"
    env.write_text(json.dumps({"db": {"host": "db.internal"}}))

    rc = main([str(base), str(env)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["config"] == {"db": {"host": "db.internal", "port": 5432}}
    assert "__provenance__" not in out


def test_cli_with_provenance(tmp_path: Path, capsys):
    base = tmp_path / "base.json"
    base.write_text(json.dumps({"a": 1}))
    env = tmp_path / "env.json"
    env.write_text(json.dumps({"a": 2}))

    rc = main(["--provenance", str(base), str(env)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["__provenance__"] == {"a": "env.json"}