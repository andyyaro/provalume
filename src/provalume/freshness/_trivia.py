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

Two implementation decisions the contract implies but does not spell out, both
taken in the escalating direction:

*Token identity is ``(type, text)``, never position.* Line and column numbers
move under every whitespace edit, so comparing them would make the first rule
unreachable. ``NEWLINE`` text is canonicalised to ``""`` because it holds a
line terminator and nothing else — ``"\\n"``, ``"\\r\\n"``, or ``""`` when the
file does not end in a newline. Without that, adding a trailing newline (which
turns the final ``NEWLINE`` token's text from ``""`` into ``"\\n"``) would read
as a semantic change. ``INDENT`` text is *not* canonicalised: it is the literal
indentation, so a re-indentation from four spaces to two returns ``None`` here
and reaches the pipeline's AST backstop, which lands it irrelevant as
``WHITESPACE_ONLY`` (or ``COMMENT_ONLY`` when the comment tokens also
differ). A conservative path, never a false ``current``.

*Every trivia answer is confirmed against raw AST equality.* Whitespace and
comment edits cannot change the tree, so requiring ``ast.dump`` equality costs
no legitimate classification — and it is defence in depth against token-level
blind spots. The sharpest known one is a lone-``\\r`` divergence:
``ast.parse`` treats each ``\\r`` as a line break while ``tokenize`` does not,
so two files differing only in how deeply one statement is indented — one
statement genuinely moving between blocks — produce byte-identical token
streams. That input cannot currently arrive through the watcher (``git``
plumbing decodes in text mode, which normalises ``\\r``), so the cross-check
is a guard on this function's own contract for any direct caller, not a hole
reachable in production.
"""

from __future__ import annotations

import ast
import io
import logging
import tokenize
from typing import Final

from provalume.freshness.relevance import strip_docstrings
from provalume.schemas.freshness import ReasonCode

log = logging.getLogger("provalume.freshness")

#: Dropped from every comparison. ``NL`` terminates a blank or comment-only
#: physical line, and no Python semantics depend on one. ``INDENT``,
#: ``DEDENT`` and ``NEWLINE`` are deliberately absent: they carry block
#: structure and statement boundaries.
_BLANK_LINE_TOKENS: Final[frozenset[int]] = frozenset({tokenize.NL})

#: Tokens kept but stripped of their text, which is a line terminator and
#: nothing else. See the module docstring: this is what makes "trailing
#: newline added" whitespace rather than a change.
_LINE_TERMINATOR_TOKENS: Final[frozenset[int]] = frozenset({tokenize.NEWLINE})

#: A token stream reduced to what is allowed to matter.
_TokenPairs = list[tuple[int, str]]


def classify(pre: str, post: str) -> ReasonCode | None:
    """Name the change if it is trivia; ``None`` for anything else or unsure."""
    try:
        pre_tokens = _token_pairs(pre)
        post_tokens = _token_pairs(post)
        if pre_tokens is None or post_tokens is None:
            return None

        pre_tree = ast.parse(pre)
        post_tree = ast.parse(post)
        trees_agree = ast.dump(pre_tree) == ast.dump(post_tree)

        if pre_tokens == post_tokens:
            # Whitespace cannot move the tree. If the tree moved anyway, the
            # token stream is lying to us (see the module docstring) and the
            # only safe answer is to escalate.
            return ReasonCode.WHITESPACE_ONLY if trees_agree else None

        if _without_comments(pre_tokens) == _without_comments(post_tokens):
            return ReasonCode.COMMENT_ONLY if trees_agree else None

        # Rule three. Raw inequality is part of the contract: an AST-equal
        # change the first two rules could not name is the pipeline backstop's
        # to label, not ours.
        if not trees_agree and ast.dump(strip_docstrings(pre_tree)) == ast.dump(
            strip_docstrings(post_tree)
        ):
            return ReasonCode.DOCSTRING_ONLY
    except Exception:
        log.debug("trivia classification failed; escalating", exc_info=True)
        return None
    return None  # more than trivia: the pipeline names it from here.


def _token_pairs(source: str) -> _TokenPairs | None:
    """``(type, text)`` for every token that is allowed to matter, or ``None``
    when ``tokenize`` refuses a source ``ast.parse`` accepted."""
    try:
        stream = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (SyntaxError, ValueError, UnicodeError, tokenize.TokenError):
        log.debug("tokenize refused a parseable source; escalating", exc_info=True)
        return None
    return [
        (token.type, "" if token.type in _LINE_TERMINATOR_TOKENS else token.string)
        for token in stream
        if token.type not in _BLANK_LINE_TOKENS
    ]


def _without_comments(pairs: _TokenPairs) -> _TokenPairs:
    return [pair for pair in pairs if pair[0] != tokenize.COMMENT]
