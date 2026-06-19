"""Tests for the CLI loader."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from scheduler import Scheduler, Task, Interval
from scheduler.cli import _load


def test_loads_scheduler_instance(tmp_path: Path):
    p = tmp_path / "jobs.py"
    p.write_text(textwrap.dedent("""
        from scheduler import Scheduler, Task, Interval
        SCHEDULER = Scheduler([Task("a", lambda: None, Interval(seconds=1))])
    """))
    s = _load(p)
    assert isinstance(s, Scheduler)
    assert [t.name for t in s.tasks()] == ["a"]


def test_loads_task_list(tmp_path: Path):
    p = tmp_path / "jobs.py"
    p.write_text(textwrap.dedent("""
        from scheduler import Task, Interval
        SCHEDULER = [Task("a", lambda: None, Interval(seconds=1))]
    """))
    s = _load(p)
    assert isinstance(s, Scheduler)
    assert [t.name for t in s.tasks()] == ["a"]


def test_rejects_file_without_scheduler(tmp_path: Path):
    p = tmp_path / "jobs.py"
    p.write_text("x = 1\n")
    with pytest.raises(SystemExit):
        _load(p)
