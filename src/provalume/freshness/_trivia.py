"""Trivia classification: is the change only whitespace, comments, or
docstrings?

The short-circuit stage of the relevance filter (spec §5.3): these three
classes leave a record ``current`` without a re-run. Everything here is
stdlib ``tokenize``/``ast`` (I1) and biased to escalate — returning ``None``
means "more than trivia, or unsure", and the pipeline treats both as
escalation material.

Contract (frozen at the M3 skeleton; ``relevance.assess_file`` calls exactly
this):

``classify(pre: str, post: str) -> ReasonCode | None``

- Called only when BOTH sides parse as Python (the pipeline has already
  handled missing and unparseable files). Never raises; any internal failure
  (tokenize errors included — a file can parse yet fail tokenize in exotic
  encodings) returns ``None``.
- ``WHITESPACE_ONLY``: the token streams are identical ignoring only ``NL``
  tokens (blank lines) — intra-line spacing never reaches tokens, and
  ``INDENT``/``DEDENT``/``NEWLINE`` are **semantic in Python and must not be
  ignored**. Trailing-whitespace and blank-line changes land here.
- ``COMMENT_ONLY``: not whitespace-only, but the streams are identical when
  ``COMMENT`` tokens are also dropped.
- ``DOCSTRING_ONLY``: neither of the above, but the ASTs are equal after
  docstring stripping (use ``relevance.strip_docstrings``) while the raw
  ASTs differ — i.e. the only tree difference is docstring text.
- Anything else → ``None``.
- Deterministic: same inputs, same answer, no environment dependence.
"""

from __future__ import annotations

from provalume.schemas.freshness import ReasonCode


def classify(pre: str, post: str) -> ReasonCode | None:
    """Not yet implemented (M3 fan-out unit); escalate until it is."""
    return None
