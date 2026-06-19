"""Tests for jobflow."""

from __future__ import annotations

import textwrap

import pytest

import jobflow
from jobflow import Job, JobFlow, JobStatus, run_flow
from jobflow.flow import FlowError


# ---------------------------------------------------------------------- #
# Basic ordering
# ---------------------------------------------------------------------- #


def test_runs_jobs_in_dependency_order():
    order: list[str] = []

    def a():
        order.append("a")
        return "A"

    def b(a_result):
        order.append("b")
        assert a_result == "A"
        return "B"

    def c(b_result):
        order.append("c")
        assert b_result == "B"
        return "C"

    ja = Job(a, name="a")
    jb = Job(b, name="b", depends_on=[ja])
    jc = Job(c, name="c", depends_on=[jb])

    report = JobFlow([jc], name="t").run()
    assert order == ["a", "b", "c"]
    assert report.ok
    assert [r.status for r in report.results] == [
        JobStatus.SUCCEEDED,
        JobStatus.SUCCEEDED,
        JobStatus.SUCCEEDED,
    ]


def test_diamond_dependency_runs_each_job_once():
    counts = {"left": 0, "right": 0, "join": 0}

    def start():
        return 1

    def left(start_result):
        counts["left"] += 1
        return start_result + 1

    def right(start_result):
        counts["right"] += 1
        return start_result + 2

    def join(left_result, right_result):
        counts["join"] += 1
        return left_result + right_result

    s = Job(start, name="start")
    l = Job(left, name="left", depends_on=[s])
    r = Job(right, name="right", depends_on=[s])
    j = Job(join, name="join", depends_on=[l, r])

    report = JobFlow([j]).run()
    assert report.ok
    assert counts == {"left": 1, "right": 1, "join": 1}
    assert j.result.value == 5  # (1+1) + (1+2)


def test_independent_jobs_run_in_declaration_order():
    seen: list[str] = []

    def make(name):
        def f():
            seen.append(name)
            return name
        return f

    flow = JobFlow(
        [Job(make("x"), name="x"), Job(make("y"), name="y"), Job(make("z"), name="z")]
    )
    report = flow.run()
    assert seen == ["x", "y", "z"]
    assert report.ok


def test_add_dependency_chaining():
    a = Job(lambda: 1, name="a")
    b = Job(lambda x: x + 1, name="b")
    c = Job(lambda x: x * 10, name="c")
    c.add_dependency(b).add_dependency(a)
    assert [d.name for d in c.depends_on] == ["b", "a"]


def test_add_dependency_self_raises():
    a = Job(lambda: 1, name="a")
    with pytest.raises(ValueError):
        a.add_dependency(a)


def test_add_dependency_rejects_non_job():
    a = Job(lambda: 1, name="a")
    with pytest.raises(TypeError):
        a.add_dependency("not a job")  # type: ignore[arg-type]


def test_job_must_wrap_callable():
    with pytest.raises(TypeError):
        Job(42)  # type: ignore[arg-type]


# ---------------------------------------------------------------------- #
# Failure handling
# ---------------------------------------------------------------------- #


def test_failure_stops_downstream_jobs():
    def boom():
        raise RuntimeError("boom")

    def after():
        return "should not run"

    a = Job(boom, name="boom")
    b = Job(after, name="after", depends_on=[a])

    report = JobFlow([a, b]).run()
    assert not report.ok
    assert [r.status for r in report.results] == [
        JobStatus.FAILED,
        JobStatus.SKIPPED,
    ]
    assert b.result.status == JobStatus.SKIPPED
    assert b.result.error is not None
    assert "boom" in str(b.result.error)


def test_failure_does_not_stop_unrelated_jobs():
    def boom():
        raise RuntimeError("boom")

    def ok():
        return "ok"

    a = Job(boom, name="boom")
    b = Job(ok, name="ok")
    flow = JobFlow([a, b])

    # Default: stop_on_failure=True, but b doesn't depend on a, so b still runs.
    report = flow.run()
    assert not report.ok
    statuses = {r.job.name: r.status for r in report.results}
    assert statuses == {"boom": JobStatus.FAILED, "ok": JobStatus.SUCCEEDED}


def test_keep_going_runs_every_job():
    def boom():
        raise RuntimeError("boom")

    def ok():
        return "ok"

    a = Job(boom, name="boom")
    b = Job(ok, name="ok", depends_on=[a])
    c = Job(ok, name="also_ok")
    report = JobFlow([a, b, c]).run(stop_on_failure=False)
    statuses = {r.job.name: r.status for r in report.results}
    assert statuses == {
        "boom": JobStatus.FAILED,
        "ok": JobStatus.SKIPPED,
        "also_ok": JobStatus.SUCCEEDED,
    }
    assert not report.ok


def test_failure_records_exception():
    class MyError(Exception):
        pass

    def boom():
        raise MyError("nope")

    a = Job(boom, name="boom")
    report = JobFlow([a]).run()
    assert a.result.status == JobStatus.FAILED
    assert isinstance(a.result.error, MyError)
    assert str(a.result.error) == "nope"


# ---------------------------------------------------------------------- #
# Validation: cycles and missing deps
# ---------------------------------------------------------------------- #


def test_cycle_detected():
    a = Job(lambda: 1, name="a")
    b = Job(lambda x: x, name="b", depends_on=[a])
    a.depends_on.append(b)  # create a -> b -> a cycle

    with pytest.raises(FlowError) as excinfo:
        JobFlow([a, b]).run()
    assert "cycle" in str(excinfo.value).lower()


def test_self_cycle_detected_via_run():
    a = Job(lambda: 1, name="a")
    a.depends_on.append(a)  # bypass add_dependency's guard
    with pytest.raises(FlowError):
        JobFlow([a]).run()


def test_missing_dependency_detected():
    orphan = Job(lambda: 1, name="orphan")
    a = Job(lambda x: x, name="a", depends_on=[orphan])
    # Build a flow that doesn't include `orphan`.
    with pytest.raises(FlowError) as excinfo:
        JobFlow([a]).run()
    assert "orphan" in str(excinfo.value)


# ---------------------------------------------------------------------- #
# Re-running
# ---------------------------------------------------------------------- #


def test_flow_can_be_rerun():
    counter = {"n": 0}

    def inc():
        counter["n"] += 1
        return counter["n"]

    a = Job(inc, name="inc")
    flow = JobFlow([a])
    flow.run()
    flow.run()
    assert counter["n"] == 2
    assert a.result.value == 2


# ---------------------------------------------------------------------- #
# Hooks
# ---------------------------------------------------------------------- #


def test_hooks_are_called():
    events: list[tuple[str, str]] = []

    def a():
        return 1

    def b(x):
        return x + 1

    ja = Job(a, name="a")
    jb = Job(b, name="b", depends_on=[ja])
    flow = JobFlow([jb])
    flow.run(
        on_job_start=lambda j: events.append(("start", j.name)),
        on_job_finish=lambda r: events.append(("finish", r.job.name)),
    )
    assert events == [
        ("start", "a"),
        ("finish", "a"),
        ("start", "b"),
        ("finish", "b"),
    ]


# ---------------------------------------------------------------------- #
# Public API surface
# ---------------------------------------------------------------------- #


def test_public_api_exports():
    assert jobflow.Job is Job
    assert jobflow.JobFlow is JobFlow
    assert jobflow.run_flow is run_flow
    assert hasattr(jobflow, "JobStatus")
    assert hasattr(jobflow, "FlowError")


def test_run_flow_helper():
    a = Job(lambda: 42, name="a")
    report = run_flow(JobFlow([a]))
    assert report.ok
    assert a.result.value == 42


# ---------------------------------------------------------------------- #
# CLI
# ---------------------------------------------------------------------- #


def test_cli_runs_a_flow(tmp_path, capsys):
    script = tmp_path / "flow.py"
    script.write_text(
        textwrap.dedent(
            """
            from jobflow import Job, JobFlow

            def hello():
                return "hi"

            def shout(prev):
                return prev.upper()

            a = Job(hello, name="hello")
            b = Job(shout, name="shout", depends_on=[a])
            flow = JobFlow([b], name="cli-demo")
            """
        )
    )
    from jobflow.__main__ import main

    rc = main([str(script)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "cli-demo" in out
    assert "succeeded" in out
    assert "hello" in out and "shout" in out


def test_cli_picks_named_flow(tmp_path, capsys):
    script = tmp_path / "flow.py"
    script.write_text(
        textwrap.dedent(
            """
            from jobflow import Job, JobFlow

            def f():
                return 1

            unused = JobFlow([Job(f)], name="unused")
            wanted = JobFlow([Job(f)], name="wanted")
            """
        )
    )
    from jobflow.__main__ import main

    rc = main([str(script), "--flow", "wanted"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "wanted" in out
    assert "unused" not in out


def test_cli_reports_failure(tmp_path, capsys):
    script = tmp_path / "flow.py"
    script.write_text(
        textwrap.dedent(
            """
            from jobflow import Job, JobFlow

            def boom():
                raise RuntimeError("kaboom")

            flow = JobFlow([Job(boom, name="boom")], name="bad")
            """
        )
    )
    from jobflow.__main__ import main

    rc = main([str(script)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "failed" in out
    assert "kaboom" in out


def test_cli_rejects_missing_file(capsys):
    from jobflow.__main__ import main

    rc = main(["/no/such/file.py"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not found" in err


def test_cli_rejects_no_flow(tmp_path, capsys):
    script = tmp_path / "flow.py"
    script.write_text("x = 1\n")
    from jobflow.__main__ import main

    rc = main([str(script)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "No JobFlow" in err


def test_cli_rejects_multiple_flows_without_picker(tmp_path, capsys):
    script = tmp_path / "flow.py"
    script.write_text(
        textwrap.dedent(
            """
            from jobflow import Job, JobFlow
            flow = JobFlow([], name="a")
            other = JobFlow([], name="b")
            """
        )
    )
    from jobflow.__main__ import main

    rc = main([str(script)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Multiple" in err


def test_cli_quiet_suppresses_progress(tmp_path, capsys):
    script = tmp_path / "flow.py"
    script.write_text(
        textwrap.dedent(
            """
            from jobflow import Job, JobFlow
            flow = JobFlow([Job(lambda: 1, name="only")], name="q")
            """
        )
    )
    from jobflow.__main__ import main

    rc = main([str(script), "--quiet"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "-> start" not in out
    assert "FlowReport" in out


def test_cli_keep_going(tmp_path, capsys):
    script = tmp_path / "flow.py"
    script.write_text(
        textwrap.dedent(
            """
            from jobflow import Job, JobFlow

            def boom():
                raise RuntimeError("nope")

            def ok():
                return 1

            a = Job(boom, name="boom")
            b = Job(ok, name="ok", depends_on=[a])
            c = Job(ok, name="independent")
            flow = JobFlow([a, b, c], name="kg")
            """
        )
    )
    from jobflow.__main__ import main

    rc = main([str(script), "--keep-going"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "skipped" in out
    assert "succeeded" in out
