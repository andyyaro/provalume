"""FTS5 query construction and safety (threat T22).

User text never reaches FTS5 as query syntax. It is tokenised and rebuilt as
double-quoted terms, and FTS5 operators, column filters, and prefix wildcards are
**stripped rather than escaped**.

Stripping over escaping is deliberate. Escaping invites a bypass — one missed
context, one nesting case, and the operator is live again. Stripping cannot be
bypassed because the dangerous characters do not survive tokenisation at all.

The cost is that users cannot write FTS queries: no ``NEAR``, no column filters,
no boolean operators. Accepted. A memory system's query language should not be an
injection-shaped surface, and the structured filters cover what FTS syntax would
have been used for.
"""

from __future__ import annotations

import re
from typing import Final

#: Terms are alphanumerics plus the punctuation that appears inside real
#: identifiers: dots, hyphens, underscores, slashes, colons, plus. That keeps
#: `pytest-xdist`, `no:xdist`, `src/main.py`, and `c++` searchable as single
#: terms, which matters because this corpus is mostly commands and paths.
_TERM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-/:+]*")

#: Bounds. A query is a search, not a workload (threat T24).
MAX_TERMS: Final = 32
MAX_TERM_CHARS: Final = 64
MAX_QUERY_CHARS: Final = 4_096

#: Dropped from queries: too common to discriminate, and they cost a full index
#: scan each. Kept small — over-filtering loses real signal in a corpus where
#: "no" and "not" can be the whole point ("no:xdist").
_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "to",
        "of",
        "in",
        "on",
        "at",
        "for",
        "with",
        "by",
        "from",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
    }
)


def tokenize(text: str) -> list[str]:
    """Extract safe search terms from arbitrary user input.

    Everything that is not a term character is discarded, so FTS5 operators
    (``*``, ``^``, ``:``, ``NEAR``, ``AND``, ``OR``, ``NOT``, parentheses,
    quotes) cannot survive. Bare boolean keywords are dropped by name as well,
    since they are otherwise valid words.
    """
    if not text:
        return []
    text = text[:MAX_QUERY_CHARS]
    terms: list[str] = []
    for match in _TERM.finditer(text):
        term = match.group(0)[:MAX_TERM_CHARS]
        lowered = term.lower()
        # FTS5 treats bare uppercase AND/OR/NOT/NEAR as operators. They are also
        # rarely meaningful as search terms.
        if lowered in {"and", "or", "not", "near"}:
            continue
        if lowered in _STOPWORDS:
            continue
        # A term of only punctuation after the leading alphanumeric is noise.
        if not any(c.isalnum() for c in term):
            continue
        terms.append(term)
        if len(terms) >= MAX_TERMS:
            break
    return terms


def quote_term(term: str) -> str:
    """Wrap a term as an FTS5 string literal.

    FTS5 escapes a double quote by doubling it. Since :func:`tokenize` already
    removed quotes, this is belt-and-braces for callers that build a query from a
    term they sourced elsewhere.
    """
    return '"' + term.replace('"', '""') + '"'


def build_query(text: str, *, mode: str = "or") -> str:
    """Build a safe FTS5 MATCH expression, or ``""`` if nothing is searchable.

    ``mode="or"`` is the default because memory retrieval is a ranked search, not
    a filter: a query of four terms should surface a record matching three of
    them, with BM25 handling the ordering. ``mode="and"`` is available for
    callers that genuinely need conjunction.

    An empty return means the caller should fall back to structured filtering
    rather than running a MATCH — an empty MATCH expression is a syntax error in
    FTS5, and a bare ``""`` would match nothing at all.
    """
    terms = tokenize(text)
    if not terms:
        return ""
    joiner = " AND " if mode == "and" else " OR "
    return joiner.join(quote_term(t) for t in terms)


def build_phrase_query(text: str) -> str:
    """Build an exact-phrase query from user text."""
    terms = tokenize(text)
    if not terms:
        return ""
    return quote_term(" ".join(terms))


def is_safe_query(expression: str) -> bool:
    """Whether an expression contains only quoted terms and joiners.

    Used by ``tests/security/test_fts_safety.py`` to assert that no adversarial
    input produces an expression carrying live FTS5 syntax.
    """
    if not expression:
        return True
    stripped = re.sub(r'"(?:[^"]|"")*"', "", expression)
    remainder = stripped.replace(" AND ", " ").replace(" OR ", " ").strip()
    return remainder == ""


#: FTS5's bm25() returns a *negative* score where lower is better. Every use
#: flips the sign, so this constant documents the convention in one place rather
#: than as a stray minus sign at each call site.
BM25_IS_NEGATIVE: Final = True


def normalize_bm25(raw_scores: list[float]) -> list[float]:
    """Min-max normalise flipped BM25 scores into ``[0, 1]``.

    Normalisation is per-query over the candidate set, because BM25 has no
    absolute scale: a score of 8 means something different for a two-term query
    than a ten-term one, so cross-query comparison would be meaningless.

    A single candidate, or a set where every score is identical, normalises to
    ``1.0`` — with nothing to compare against, the only honest relative relevance
    is "as relevant as anything else here".
    """
    if not raw_scores:
        return []
    flipped = [-s for s in raw_scores] if BM25_IS_NEGATIVE else list(raw_scores)
    lo, hi = min(flipped), max(flipped)
    if hi - lo < 1e-12:
        return [1.0] * len(flipped)
    return [(s - lo) / (hi - lo) for s in flipped]
