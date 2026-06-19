"""A small, standard-library-only feature flag library.

Usage::

    from featureflags import FlagClient

    flags = FlagClient.from_file("flags.json")
    if flags.is_enabled("new-dashboard", context={"user_id": "alice"}):
        ...

Flags are defined in a JSON file you can edit without redeploying. Each flag
is one of:

* ``off``      -- always disabled.
* ``on``       -- always enabled.
* ``rollout``  -- enabled for a slice of traffic, described by a ``rules``
                  object (see :class:`featureflags.client.FlagClient`).

Asking about a flag that nobody has defined returns ``False`` (or whatever
default you pass) instead of raising, so a missing flag can't take down a
request.
"""

from .client import FlagClient
from .flags import Flag, State

__all__ = ["FlagClient", "Flag", "State"]