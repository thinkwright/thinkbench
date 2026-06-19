"""Watch a rate limit kick in without standing up the whole service.

Usage:
    python -m ratelimit_demo --capacity 5 --rate 1 --key alice --count 10
    python -m ratelimit_demo --capacity 5 --rate 1 --key alice --count 10 --sleep 0.2
"""

from __future__ import annotations

import argparse
import time

from ratelimit import Limiter


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--capacity", type=int, required=True,
                   help="max burst size (tokens)")
    p.add_argument("--rate", type=float, required=True,
                   help="refill rate (tokens/sec)")
    p.add_argument("--key", default="demo",
                   help="caller key to check against")
    p.add_argument("--count", type=int, default=10,
                   help="number of checks to perform")
    p.add_argument("--sleep", type=float, default=0.0,
                   help="seconds to sleep between checks")
    args = p.parse_args()

    limiter = Limiter(capacity=args.capacity, rate=args.rate)
    print(f"limit: capacity={args.capacity} rate={args.rate}/s key={args.key!r}")
    print()

    for i in range(1, args.count + 1):
        d = limiter.check(args.key)
        marker = "OK " if d.allowed else "NO "
        extra = "" if d.allowed else f"  retry in {d.retry_after:.3f}s"
        print(f"  #{i:>3}  {marker}  remaining={d.remaining:6.3f}{extra}")
        if args.sleep > 0:
            time.sleep(args.sleep)


if __name__ == "__main__":
    main()
