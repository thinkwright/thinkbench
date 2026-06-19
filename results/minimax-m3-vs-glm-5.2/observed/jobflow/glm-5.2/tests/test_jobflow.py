"""Tests for jobflow."""

import subprocess
import sys

import pytest

import jobflow
from jobflow import Flow, FlowError, Job, Status, run


# --------------------------------------------------------------------------- #
# Job construction
# --------------------------------------------------------------------------- #

def test_job_requires_func_or_command():
    with pytest.raises(ValueError):
        Job("x")


def test_job_cannot_have_both_func_and_command():
    with pytest.raises(ValueError):
        Job("x", func=lambda: 1, command="echo hi")


def test_job_name_must_be_nonempty():
    with pytest.raises(ValueError):
        Job("", func=lambda: 1)


def test_job_needs_normalized_from_job_objects():
    a = Job("a", func=lambda: 1)
    b = Job("b", func=lambda: 1, needs=[a])
    assert b.needs == ["a"]


def test_job_needs_single_string():
    j = Job("b", func=lambda: 1, needs="a")
    assert j.needs == ["a"]


def test_job_needs_duplicate_rejected():
    with pytest.raises(ValueError):
        Job("b", func=lambda: 1, needs=["a", "a"])


def test_job_needs_bad_type_rejected():
    with pytest.raises(TypeError):
        Job("b", func=lambda: 1, needs=[123])


def test_job_rshift_chaining():
    a = Job("a", func=lambda: 1)
    b = Job("b", func=lambda: 1)
    c = Job("c", func=lambda: 1)
    # a >> b means b needs a
    a >> b >> c
    assert b.needs == ["a"]
    assert c.needs == ["b"]


def test_job_lshift_chaining():
    a = Job("a", func=lambda: 1)
    b = Job("b", func=lambda: 1)
    # b << a means b needs a
    b << a
    assert b.needs == ["a"]


def test_job_func_with_args_kwargs():
    calls = []

    def f(x, y, z=0):
        calls.append((x, y, z))
        return x + y + z

    j = Job("j", func=f, args=(1, 2), kwargs={"z": 3})
    assert j.execute() == 6
    assert calls == [(1, 2, 3)]


def test_job_command_runs_and_returns_stdout():
    j = Job("echo", command="echo hello")
    assert j.execute().strip() == "hello"


def test_job_command_list_form():
    j = Job("echo", command=["echo", "world"])
    assert j.execute().strip() == "world"


def test_job_command_failure_raises():
    j = Job("fail", command="python -c 'import sys; sys.exit(3)'")
    with pytest.raises(subprocess.CalledProcessError):
        j.execute()


def test_job_command_empty_rejected():
    j = Job("empty", command="")
    with pytest.raises(ValueError):
        j.execute()


# --------------------------------------------------------------------------- #
# Flow construction & validation
# --------------------------------------------------------------------------- #

def test_flow_rejects_non_job():
    with pytest.raises(TypeError):
        Flow(["not a job"])


def test_flow_rejects_duplicate_names():
    with pytest.raises(ValueError):
        Flow([Job("a", func=lambda: 1), Job("a", func=lambda: 2)])


def test_flow_rejects_unknown_dependency():
    with pytest.raises(FlowError):
        Flow([Job("a", func=lambda: 1, needs=["nope"])])


def test_flow_rejects_self_dependency():
    with pytest.raises(FlowError):
        Flow([Job("a", func=lambda: 1, needs=["a"])])


def test_flow_rejects_cycle():
    flow = Flow([
        Job("a", func=lambda: 1, needs=["c"]),
        Job("b", func=lambda: 1, needs=["a"]),
        Job("c", func=lambda: 1, needs=["b"]),
    ])
    with pytest.raises(FlowError):
        flow._topological_order()


def test_flow_diamond_is_valid():
    flow = Flow([
        Job("a", func=lambda: 1),
        Job("b", func=lambda: 1, needs=["a"]),
        Job("c", func=lambda: 1, needs=["a"]),
        Job("d", func=lambda: 1, needs=["b", "c"]),
    ])
    order = flow._topological_order()
    assert order.index("a") < order.index("b")
    assert order.index("a") < order.index("c")
    assert order.index("b") < order.index("d")
    assert order.index("c") < order.index("d")


def test_flow_len_contains_getitem_iter():
    a = Job("a", func=lambda: 1)
    b = Job("b", func=lambda: 1, needs=["a"])
    flow = Flow([a, b])
    assert len(flow) == 2
    assert "a" in flow
    assert flow["b"] is b
    assert {j.name for j in flow} == {"a", "b"}


# --------------------------------------------------------------------------- #
# Running
# --------------------------------------------------------------------------- #

def test_run_executes_in_dependency_order():
    log = []
    flow = Flow([
        Job("a", func=lambda: log.append("a")),
        Job("b", func=lambda: log.append("b"), needs=["a"]),
        Job("c", func=lambda: log.append("c"), needs=["b"]),
    ])
    result = flow.run()
    assert log == ["a", "b", "c"]
    assert result.succeeded
    assert [r.name for r in result] == ["a", "b", "c"]


def test_run_returns_return_values():
    flow = Flow([
        Job("a", func=lambda: 42),
        Job("b", func=lambda: "x", needs=["a"]),
    ])
    result = flow.run()
    assert result["a"].return_value == 42
    assert result["b"].return_value == "x"


def test_run_skips_dependents_on_failure():
    log = []

    def fail():
        raise RuntimeError("boom")

    flow = Flow([
        Job("a", func=lambda: log.append("a")),
        Job("b", func=fail, needs=["a"]),
        Job("c", func=lambda: log.append("c"), needs=["b"]),
        Job("d", func=lambda: log.append("d"), needs=["a"]),
    ])
    result = flow.run()

    assert result["a"].status is Status.SUCCESS
    assert result["b"].status is Status.FAILED
    assert result["c"].status is Status.SKIPPED
    assert result["d"].status is Status.SUCCESS  # independent of b
    assert log == ["a", "d"]
    assert not result.succeeded
    assert [r.name for r in result.failed] == ["b"]
    assert [r.name for r in result.skipped] == ["c"]


def test_run_failure_records_error_and_traceback():
    def fail():
        raise ValueError("nope")

    flow = Flow([Job("a", func=fail)])
    result = flow.run()
    assert isinstance(result["a"].error, ValueError)
    assert "ValueError" in result["a"].traceback
    assert "nope" in result["a"].traceback


def test_run_cascade_skip():
    def fail():
        raise RuntimeError("x")

    flow = Flow([
        Job("a", func=fail),
        Job("b", func=lambda: 1, needs=["a"]),
        Job("c", func=lambda: 1, needs=["b"]),
        Job("d", func=lambda: 1, needs=["c"]),
    ])
    result = flow.run()
    assert result["a"].status is Status.FAILED
    for name in ("b", "c", "d"):
        assert result[name].status is Status.SKIPPED


def test_run_dry_run_executes_nothing():
    log = []
    flow = Flow([
        Job("a", func=lambda: log.append("a")),
        Job("b", func=lambda: log.append("b"), needs=["a"]),
    ])
    result = flow.run(dry_run=True)
    assert log == []
    assert all(r.status is Status.SKIPPED for r in result)


def test_run_only_runs_target_and_its_deps():
    log = []
    flow = Flow([
        Job("a", func=lambda: log.append("a")),
        Job("b", func=lambda: log.append("b"), needs=["a"]),
        Job("c", func=lambda: log.append("c"), needs=["b"]),
    ])
    result = flow.run(only=["b"])
    assert set(log) == {"a", "b"}
    assert "c" not in result
    assert result.succeeded


def test_run_only_unknown_job_raises():
    flow = Flow([Job("a", func=lambda: 1)])
    with pytest.raises(FlowError):
        flow.run(only=["nope"])


def test_run_on_event_callback():
    seen = []
    flow = Flow([
        Job("a", func=lambda: 1),
        Job("b", func=lambda: 1, needs=["a"]),
    ])
    flow.run(on_event=lambda r: seen.append((r.name, r.status)))
    assert seen == [
        ("a", Status.SUCCESS),
        ("b", Status.SUCCESS),
    ]


def test_module_level_run_function():
    flow = Flow([Job("a", func=lambda: 1)])
    result = run(flow)
    assert result.succeeded


def test_module_level_run_rejects_non_flow():
    with pytest.raises(TypeError):
        run("not a flow")


def test_run_result_dunder_access():
    flow = Flow([Job("a", func=lambda: 1)])
    result = flow.run()
    assert "a" in result
    assert result["a"].succeeded
    assert [r.name for r in result] == ["a"]


def test_jobresult_duration():
    flow = Flow([Job("a", func=lambda: 1)])
    result = flow.run()
    assert result["a"].duration is not None
    assert result["a"].duration >= 0


# --------------------------------------------------------------------------- #
# Topological order determinism
# --------------------------------------------------------------------------- #

def test_topological_order_is_deterministic():
    flow = Flow([
        Job("c", func=lambda: 1),
        Job("a", func=lambda: 1),
        Job("b", func=lambda: 1),
    ])
    # No dependencies -> alphabetical.
    assert flow._topological_order() == ["a", "b", "c"]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def test_cli_runs_flow(tmp_path, capsys):
    pipeline = tmp_path / "pipeline.py"
    pipeline.write_text(
        "import jobflow\n"
        "from jobflow import Job, Flow\n"
        "flow = Flow([\n"
        "    Job('a', func=lambda: print('ran-a')),\n"
        "    Job('b', func=lambda: print('ran-b'), needs=['a']),\n"
        "])\n"
    )
    from jobflow.__main__ import main

    rc = main([str(pipeline)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ran-a" in out
    assert "ran-b" in out
    assert "[SUCCESS] a" in out
    assert "[SUCCESS] b" in out


def test_cli_make_flow(tmp_path, capsys):
    pipeline = tmp_path / "pipeline.py"
    pipeline.write_text(
        "from jobflow import Job, Flow\n"
        "def make_flow():\n"
        "    return Flow([Job('only', func=lambda: 1)])\n"
    )
    from jobflow.__main__ import main

    rc = main([str(pipeline)])
    assert rc == 0
    assert "[SUCCESS] only" in capsys.readouterr().out


def test_cli_list(tmp_path, capsys):
    pipeline = tmp_path / "pipeline.py"
    pipeline.write_text(
        "from jobflow import Job, Flow\n"
        "flow = Flow([\n"
        "    Job('a', func=lambda: 1),\n"
        "    Job('b', func=lambda: 1, needs=['a']),\n"
        "])\n"
    )
    from jobflow.__main__ import main

    rc = main([str(pipeline), "--list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "a  (needs: -)" in out
    assert "b  (needs: a)" in out


def test_cli_only(tmp_path, capsys):
    pipeline = tmp_path / "pipeline.py"
    pipeline.write_text(
        "from jobflow import Job, Flow\n"
        "flow = Flow([\n"
        "    Job('a', func=lambda: 1),\n"
        "    Job('b', func=lambda: 1, needs=['a']),\n"
        "    Job('c', func=lambda: 1, needs=['b']),\n"
        "])\n"
    )
    from jobflow.__main__ import main

    rc = main([str(pipeline), "--only", "b"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[SUCCESS] a" in out
    assert "[SUCCESS] b" in out
    assert "c" not in out.replace("pipeline.py", "")


def test_cli_dry_run(tmp_path, capsys):
    pipeline = tmp_path / "pipeline.py"
    pipeline.write_text(
        "from jobflow import Job, Flow\n"
        "flow = Flow([Job('a', func=lambda: 1)])\n"
    )
    from jobflow.__main__ import main

    rc = main([str(pipeline), "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[SKIPPED] a" in out


def test_cli_failure_exit_code(tmp_path, capsys):
    pipeline = tmp_path / "pipeline.py"
    pipeline.write_text(
        "from jobflow import Job, Flow\n"
        "def boom():\n"
        "    raise RuntimeError('explode')\n"
        "flow = Flow([\n"
        "    Job('a', func=boom),\n"
        "    Job('b', func=lambda: 1, needs=['a']),\n"
        "])\n"
    )
    from jobflow.__main__ import main

    rc = main([str(pipeline)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "[FAILED] a" in out
    assert "[SKIPPED] b" in out
    assert "explode" in out


def test_cli_missing_file():
    from jobflow.__main__ import main

    with pytest.raises(SystemExit):
        main(["does_not_exist.py"])


def test_cli_no_flow_defined(tmp_path):
    pipeline = tmp_path / "pipeline.py"
    pipeline.write_text("x = 1\n")
    from jobflow.__main__ import main

    with pytest.raises(SystemExit):
        main([str(pipeline)])


def test_cli_runs_as_module(tmp_path):
    pipeline = tmp_path / "pipeline.py"
    pipeline.write_text(
        "from jobflow import Job, Flow\n"
        "flow = Flow([Job('a', func=lambda: 1)])\n"
    )
    proc = subprocess.run(
        [sys.executable, "-m", "jobflow", str(pipeline)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "[SUCCESS] a" in proc.stdout