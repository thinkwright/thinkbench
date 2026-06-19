"""mergeconf: merge configuration from several sources into one effective view.

Sources are merged in precedence order: earlier sources are lower precedence,
later sources win. Dicts are merged recursively; non-dict values from a higher
precedence source replace whatever was there. The result carries provenance so
you can answer "why is this value what it is?".
"""

from .merge import mergeconf, MergeResult, Source
from .load import load_source

__all__ = ["mergeconf", "MergeResult", "Source", "load_source"]