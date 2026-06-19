"""Command-line interface for exercising :mod:`ratelimit` without a service.

Run a burst of checks against a single caller and print each decision, so you
can watch the limit kick in::

    $ python -m ratelimit --rate 2 --capacity 2 --caller abc --count 5
    [ 1] allowed=True  tokens_remaining=1.000000
    [ 2] allowed=True  tokens_remaining=0.000000
    [ 3] allowed=False tokens_remaining=0.000000 retry_after=0.500000
    [ 4] allowed=False tokens_remaining=0.000000 retry_after=0.500000
    [ 5] allowed=False tokens_remaining=0.000000 retry_after=0.500000
    allowed=3 denied=2

By default checks fire as fast as possible (so you see the burst). Use
``--interval`` to space them out and watch tokens refill.
"""

from __future__ import annotations

import argparse
import sys
import time

from . import Limiter, LimiterError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ratelimit",
        description="Exercise the ratelimit limiter from the command line.",
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=5.0,
        help="Sustained refill rate in tokens/second (default: 5).",
    )
    parser.add_argument(
        "--capacity",
        type=float,
        default=5.0,
        help="Bucket capacity / max burst (default: 5).",
    )
    parser.add_argument(
        "--caller",
        default="cli-caller",
        help="Caller identifier to check on behalf of (default: cli-caller).",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=10,
        help="Number of checks to perform (default: 10).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.0,
        help="Seconds to sleep between checks (default: 0, fire as fast as possible).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        limiter = Limiter(rate=args.rate, capacity=args.capacity)
    except LimiterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    allowed = denied = 0
    for i in range(1, args.count + 1):
        decision = limiter.check(args.caller)
        if decision.allowed:
            allowed += 1
            print(
                f"[{i:2d}] allowed=True  "
                f"tokens_remaining={decision.tokens_remaining:.6f}"
            )
        else:
            denied += 1
            print(
                f"[{i:2d}] allowed=False "
                f"tokens_remaining={decision.tokens_remaining:.6f}"
                f" retry_after={decision.retry_after:.6f}"
            )
        if args.interval > 0 and i < args.count:
            time.sleep(args.interval)

    print(f"allowed={allowed} denied={denied}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())