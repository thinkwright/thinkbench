"""Command-line entry point: ``python -m jobflow <script.py>``."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from .flow import FlowError, JobFlow


def _load_module(path: Path) -> ModuleType:
    """Load a Python file as a module without executing it as __main__."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise FlowError(f"Could not load Python file: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _find_flow(module: ModuleType, attr: str | None) -> JobFlow:
    """Locate a JobFlow in the module, either by attribute name or by scanning."""
    if attr is not None:
        obj: Any = getattr(module, attr, None)
        if not isinstance(obj, JobFlow):
            raise FlowError(
                f"{attr!r} in {module.__name__} is not a JobFlow "
                f"(got {type(obj).__name__})"
            )
        return obj

    flows = [
        (name, obj)
        for name, obj in vars(module).items()
        if isinstance(obj, JobFlow) and not name.startswith("_")
    ]
    if not flows:
        raise FlowError(
            f"No JobFlow found in {module.__name__}. "
            "Define one (e.g. `flow = JobFlow([...])`) or pass --flow NAME."
        )
    if len(flows) > 1:
        names = ", ".join(n for n, _ in flows)
        raise FlowError(
            f"Multiple JobFlows found in {module.__name__}: {names}. "
            "Pass --flow NAME to pick one."
        )
    return flows[0][1]


def _print_progress(job) -> None:
    print(f"  -> start  {job.name}", flush=True)


def _print_result(result) -> None:
    extra = ""
    if result.status.value == "failed" and result.error is not None:
        extra = f"  ({type(result.error).__name__}: {result.error})"
    elif result.duration is not None:
        extra = f"  ({result.duration:.3f}s)"
    print(f"  <- {result.status.value:<9} {result.job.name}{extra}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jobflow",
        description="Run a JobFlow defined in a Python file.",
    )
    parser.add_argument("script", help="Path to a Python file defining a JobFlow.")
    parser.add_argument(
        "--flow",
        help=(
            "Name of the JobFlow variable in the script. "
            "If omitted, the only top-level JobFlow is used."
        ),
    )
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Do not stop when a job fails; run every job and report all failures.",
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true", help="Suppress per-job progress output."
    )
    args = parser.parse_args(argv)

    path = Path(args.script)
    if not path.exists():
        print(f"jobflow: file not found: {path}", file=sys.stderr)
        return 2

    try:
        module = _load_module(path)
        flow = _find_flow(module, args.flow)
    except FlowError as exc:
        print(f"jobflow: {exc}", file=sys.stderr)
        return 2

    print(f"jobflow: running {flow.name!r} from {path}", flush=True)
    on_start = None if args.quiet else _print_progress
    on_finish = None if args.quiet else _print_result
    try:
        report = flow.run(
            on_job_start=on_start,
            on_job_finish=on_finish,
            stop_on_failure=not args.keep_going,
        )
    except FlowError as exc:
        print(f"jobflow: {exc}", file=sys.stderr)
        return 2

    print(str(report))
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
