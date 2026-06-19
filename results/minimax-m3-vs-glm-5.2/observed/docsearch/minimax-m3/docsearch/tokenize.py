"""Tokenization for docsearch.

Keeps things simple: lowercase, split on non-letter/digit boundaries, drop
common English stopwords and very short tokens. No stemming — the goal is
sensible ranking, not linguistic completeness.
"""

import re
import string
from typing import Iterable

# A small, conservative stopword list. Keeping it short avoids over-pruning
# in technical / domain-specific corpora (notes, transcripts, docs).
_STOPWORDS = frozenset(
    """
    a an the and or but if then else when while of in on at to for from by
    with as is are was were be been being have has had do does did this that
    these those it its i you he she we they me him her us them my your his
    their our not no nor so too very can will just dont don't
    """.split()
)

# Match runs of letters/digits/apostrophes; apostrophes are kept so
# "don't" stays one token before stopword filtering strips it.
_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")


def tokenize(text: str) -> list[str]:
    """Lowercase `text` and return a list of meaningful tokens."""
    if not text:
        return []
    tokens = _TOKEN_RE.findall(text.lower())
    out = []
    for tok in tokens:
        if len(tok) <= 1:
            continue
        if tok in _STOPWORDS:
            continue
        # Strip leading/trailing apostrophes left over from contractions.
        tok = tok.strip("'")
        if len(tok) <= 1:
            continue
        out.append(tok)
    return out


def vocabulary(tokens: Iterable[str]) -> list[str]:
    """Unique tokens in first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out
