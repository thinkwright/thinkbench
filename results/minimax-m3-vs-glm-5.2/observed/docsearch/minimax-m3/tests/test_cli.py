"""Tests for the docsearch CLI."""

import json
import subprocess
import sys
from pathlib import Path


def run_cli(*args, cwd=None):
    return subprocess.run(
        [sys.executable, "-m", "docsearch", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def test_add_and_search(tmp_path: Path):
    idx = tmp_path / "index.json"
    doc1 = tmp_path / "a.txt"
    doc2 = tmp_path / "b.txt"
    doc1.write_text("Python type hints and gradual typing.")
    doc2.write_text("How to cook pasta with tomato sauce.")

    r = run_cli("add", str(idx), str(doc1), str(doc2))
    assert r.returncode == 0, r.stderr
    assert "added" in r.stdout

    # Index file exists and is valid JSON.
    data = json.loads(idx.read_text())
    assert len(data["docs"]) == 2

    r = run_cli("search", str(idx), "python type hints")
    assert r.returncode == 0, r.stderr
    # First result should be doc 1 (the python one).
    lines = [ln for ln in r.stdout.strip().splitlines() if ln]
    assert lines, "expected at least one result"
    assert "doc 1" in lines[0]


def test_search_no_matches(tmp_path: Path):
    idx = tmp_path / "index.json"
    doc = tmp_path / "a.txt"
    doc.write_text("cooking recipes")
    run_cli("add", str(idx), str(doc))

    r = run_cli("search", str(idx), "quantum entanglement")
    assert r.returncode == 0
    assert "no matches" in r.stdout


def test_count(tmp_path: Path):
    idx = tmp_path / "index.json"
    (tmp_path / "a.txt").write_text("alpha")
    (tmp_path / "b.txt").write_text("beta")
    run_cli("add", str(idx), str(tmp_path / "a.txt"), str(tmp_path / "b.txt"))

    r = run_cli("count", str(idx))
    assert r.returncode == 0
    assert r.stdout.strip() == "2"


def test_search_empty_index(tmp_path: Path):
    idx = tmp_path / "index.json"  # doesn't exist
    r = run_cli("search", str(idx), "anything")
    assert r.returncode == 1
    assert "no documents" in r.stderr
