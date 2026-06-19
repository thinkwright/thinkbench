"""cachelayer — a small, standard-library memoization library.

Point it at an expensive function and repeat calls with the same arguments
come back from the cache instead of redoing the work.

    import cachelayer

    @cachelayer.cache(maxsize=128)
    def lookup(user_id):
        ...  # hits the network, or chews through a computation

The cache is bounded (LRU eviction), so it won't grow without limit, and it
only returns a saved answer when a call is genuinely identical to the one that
produced it.
"""

from .cache import Cache, cache, cached

__all__ = ["Cache", "cache", "cached"]