"""Command-line entry point for jobflow.

Usage::

    python -m jobflow pipeline.py            # run the flow defined in pipeline.py
    python -m jobflow pipeline.py --only test
    python -m jobflow pipeline.py --dry-run
    python -m jobflow pipeline.py --list

The target file is a normal Python module. It must expose either a module-level
``flow`` (a :class:`jobflow.Flow`) or a callable ``make_flow()`` returning one.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import time
from typing import Optional

from . import Flow, JobResult, Status, run
from .flow import RunResult


def _load_flow(path: str) -> Flow:
    if not os.path.exists(path):
        raise SystemExit(f"jobflow: no such file: {path}")
    spec = importlib.util.spec_from_file_location("jobflow_user_flow", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit(f"jobflow: could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    # Make the file's directory importable so it can import local helpers.
    here = os.path.dirname(os.path.abspath(path))
    sys.path.insert(0, here)
    try:
        spec.loader.exec_module(module)
    finally:
        try:
            sys.path.remove(here)
        except ValueError:  # pragma: no cover
            pass

    flow = getattr(module, "flow", None)
    if flow is None:
        maker = getattr(module, "make_flow", None)
        if callable(maker):
            flow = maker()
    if not isinstance(flow, Flow):
        raise SystemExit(
            f"jobflow: {path} must define a module-level `flow` (a jobflow.Flow) "
            "or a `make_flow()` function returning one"
        )
    return flow


def _format_event(result: JobResult) -> None:
    status = result.status.value.upper()
    dur = ""
    if result.duration is not None:
        dur = f" ({result.duration:.2f}s)"
    if result.status is Status.FAILED and result.error is not None:
        print(f"  [{status}] {result.name}{dur}: {result.error}")
    elif result.status is Status.SKIPPED and result.error is not None:
        print(f"  [{status}] {result.name}: {result.error}")
    else:
        print(f"  [{status}] {result.name}{dur}")


def _print_summary(result: RunResult) -> None:
    ok = sum(1 for r in result if r.status is Status.SUCCESS)
    fail = len(result.failed)
    skip = len(result.skipped)
    total = sum(1 for _ in result)
    print(
        f"\nDone: {ok} succeeded, {fail} failed, {skip} skipped "
        f"({total} total)"
    )


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jobflow",
        description="Run a jobflow flow defined in a Python file.",
    )
    parser.add_argument(
        "file",
        help="Python file defining a `flow` or `make_flow()`",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        default=None,
        metavar="JOB",
        help="run only these job(s) and their dependencies",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="compute the order and report what would run, but execute nothing",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list the jobs in execution order and exit",
    )
    args = parser.parse_args(argv)

    flow = _load_flow(args.file)

    if args.list:
        order = flow._topological_order()  # noqa: SLF001 - intentional CLI use
        for name in order:
            job = flow[name]
            deps = ", ".join(job.needs) or "-"
            print(f"  {name}  (needs: {deps})")
        return 0

    print(f"jobflow: running {len(flow)} job(s) from {args.file}")
    start = time.time()
    result = run(flow, only=args.only, dry_run=args.dry_run, on_event=_format_event)
    elapsed = time.time() - start

    _print_summary(result)
    print(f"Elapsed: {elapsed:.2f}s")

    if result.failed:
        print("\nFailures:")
        for r in result.failed:
            if r.traceback:
                print(f"--- {r.name} ---")
                print(r.traceback.rstrip())
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())