"""A small library for describing and driving multi-step business processes.

A :class:`Process` describes the shape of a process: its stages, which stage
things start in, and the named transitions that move a thing from one stage to
another. An :class:`Instance` is a single thing being driven through a process
(an order, a claim, a ticket). You advance it by transition name, and the
library refuses moves the process does not allow -- and tells you why.

Example::

    from workflow import Process

    order = Process(
        stages=["received", "paid", "packed", "shipped",
                "cancelled", "refunded"],
        start="received",
        transitions=[
            # name,      from,         to,
            ("pay",      "received",   "paid"),
            ("pack",     "paid",       "packed"),
            ("ship",     "packed",     "shipped"),
            ("decline",  "received",   "cancelled"),
            ("refund",   "paid",       "refunded"),
        ],
    )

    o = order.start()
    o.advance("pay")      # received -> paid
    o.advance("ship")     # rejected: "ship" only runs from "packed"
    print(o.stage)        # still "paid"
"""

from .process import Process, Transition
from .instance import Instance
from .errors import Rejected, Rejection

__all__ = [
    "Process",
    "Transition",
    "Instance",
    "Rejected",
    "Rejection",
]