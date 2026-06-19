"""scheduler: a small, standard-library-only task scheduler.

Register tasks with a schedule, hand them to a Scheduler, and let it run.
Tasks fire when they're due.

    from scheduler import Scheduler, every, at

    sched = Scheduler()
    sched.add(every(minutes=5), refresh_cache)
    sched.add(at("02:30"), nightly_report)
    sched.run()
"""

from .scheduler import Scheduler
from .schedules import every, at, Schedule

__all__ = ["Scheduler", "every", "at", "Schedule"]
__version__ = "0.1.0"