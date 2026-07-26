"""The relevance filter: could this change affect the record's outcome?

A deterministic stdlib-``ast`` and ``tokenize`` comparison — never a model
call (I1; ADR-0007). Its one bias, stated in the spec and enforced here:
**when uncertain, escalate.** A false `suspect` costs one re-run; a false
`current` serves a stale fact as verified, which is the failure the whole
project exists to prevent.

Per-file pipeline (``assess_file``):

1. Both sides missing, either side unreadable/undecodable, or either side
   failing ``ast.parse`` → ``UNPARSEABLE`` (relevant — a differ that cannot
   read a file does not get to call its change harmless).
2. One side missing (the file was added or deleted) → ``BODY_CHANGED``.
3. Trivia stage (``_trivia.classify``): whitespace-only / comment-only /
   docstring-only distinctions at token level. Returns ``None`` when the
   change is more than trivia — or when it is unsure, which the pipeline
   treats identically.
4. AST backstop: raw ``ast.dump`` equality means the change cannot be
   semantic (comments and whitespace are not in the tree) → ``COMMENT_ONLY``
   even when trivia could not name it. Equal after docstring-stripping but
   unequal raw → ``DOCSTRING_ONLY``.
5. Structural stage (``_structural.classify``) on the stripped trees:
   ``SIGNATURE_CHANGED`` > ``IMPORT_CHANGED`` > ``BODY_CHANGED``.

Per-record aggregation (``assess_paths``): the verdict is ``relevant`` when
ANY file's reason escalates, and the reported reason is the most alarming
one seen (priority: unparseable > signature > import > body > docstring >
comment > whitespace).

``DIFFER_VERSION`` bumps whenever classification semantics change, so a
measurement (M5) can name the differ it measured.
"""

from __future__ import annotations

import ast
import logging
from typing import Final

from provalume.schemas.freshness import (
    IRRELEVANT_REASON_CODES,
    ReasonCode,
    RelevanceVerdict,
)

log = logging.getLogger("provalume.freshness")

#: Bump on any change to classification semantics.
DIFFER_VERSION: Final = "1"

#: Most-alarming-first, for per-record aggregation.
_REASON_PRIORITY: Final[tuple[ReasonCode, ...]] = (
    ReasonCode.UNPARSEABLE,
    ReasonCode.SIGNATURE_CHANGED,
    ReasonCode.IMPORT_CHANGED,
    ReasonCode.BODY_CHANGED,
    ReasonCode.DOCSTRING_ONLY,
    ReasonCode.COMMENT_ONLY,
    ReasonCode.WHITESPACE_ONLY,
)


def strip_docstrings(tree: ast.Module) -> ast.Module:
    """A copy of ``tree`` with every docstring removed.

    A docstring is the first statement of a module, class, or function body
    when that statement is a string constant expression. Shared by the trivia
    stage (docstring-only detection) and the pipeline's AST backstop.
    """
    import copy

    stripped = copy.deepcopy(tree)
    for node in ast.walk(stripped):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            del body[0]
    return stripped


def assess_file(pre: str | None, post: str | None) -> tuple[RelevanceVerdict, ReasonCode]:
    """One file's verdict. Deterministic; never raises; unsure → escalate."""
    try:
        if pre is None and post is None:
            return RelevanceVerdict.RELEVANT, ReasonCode.UNPARSEABLE
        if pre is None or post is None:
            return RelevanceVerdict.RELEVANT, ReasonCode.BODY_CHANGED
        try:
            pre_tree = ast.parse(pre)
            post_tree = ast.parse(post)
        except (SyntaxError, ValueError):
            return RelevanceVerdict.RELEVANT, ReasonCode.UNPARSEABLE

        from provalume.freshness import _trivia

        trivia = _trivia.classify(pre, post)
        if trivia is not None:
            return _verdict(trivia)

        if ast.dump(pre_tree) == ast.dump(post_tree):
            # Comments and whitespace are not in the tree: the change cannot
            # be semantic, even when the trivia stage could not name it.
            return _verdict(ReasonCode.COMMENT_ONLY)
        if ast.dump(strip_docstrings(pre_tree)) == ast.dump(strip_docstrings(post_tree)):
            return _verdict(ReasonCode.DOCSTRING_ONLY)

        from provalume.freshness import _structural

        return _verdict(_structural.classify(pre_tree, post_tree))
    except Exception:
        log.debug("relevance assessment failed; escalating", exc_info=True)
        return RelevanceVerdict.RELEVANT, ReasonCode.UNPARSEABLE


def assess_paths(
    files: list[tuple[str, str | None, str | None]],
) -> tuple[RelevanceVerdict, ReasonCode]:
    """A record's verdict over its intersecting files: relevant if ANY file
    escalates, reported with the most alarming reason seen."""
    if not files:
        return RelevanceVerdict.RELEVANT, ReasonCode.UNPARSEABLE
    reasons = [assess_file(pre, post)[1] for _path, pre, post in files]
    worst = min(reasons, key=_REASON_PRIORITY.index)
    return _verdict(worst)


def _verdict(reason: ReasonCode) -> tuple[RelevanceVerdict, ReasonCode]:
    if reason in IRRELEVANT_REASON_CODES:
        return RelevanceVerdict.IRRELEVANT, reason
    return RelevanceVerdict.RELEVANT, reason
