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
   semantic (comments and whitespace are not in the tree) even when the
   trivia stage could not name it. Labelled honestly: ``COMMENT_ONLY`` when
   the comment tokens differ, ``WHITESPACE_ONLY`` otherwise (reindentation,
   quote style, statement splitting — formatting, not comments). Equal after
   docstring-stripping but unequal raw → ``DOCSTRING_ONLY`` — unless
   stripping emptied the module entirely, in which case the file's whole
   content changed and it escalates as ``BODY_CHANGED``.
5. Structural stage (``_structural.classify``) on the stripped trees:
   ``SIGNATURE_CHANGED`` > ``IMPORT_CHANGED`` > ``BODY_CHANGED``.

Per-record aggregation (``assess_paths``): the verdict is ``relevant`` when
ANY file's reason escalates, and the reported reason is the most alarming
one seen (priority: unparseable > signature > body > import > docstring >
comment > whitespace — a deleted file is ``body_changed`` and must not be
outranked by a sibling's import shuffle).

The caller may pass the record's verification ``command``. Commands that
*read* comments or docstrings — linters honouring ``# noqa``/``# nosec``,
type checkers honouring ``# type:``, doctest runners executing docstrings —
falsify the premise that a comment-only change cannot change the outcome, so
for a command matching ``_COMMENT_SENSITIVE`` the irrelevant short-circuit is
suppressed and the verdict escalates with its honest reason code intact.
The marker list is a closed set, matched case-insensitively as substrings;
a wrapper that hides the tool (``make lint``) defeats it — see
LIMITATIONS §9e.

``DIFFER_VERSION`` bumps whenever classification semantics change **after a
release has shipped verdicts**, so a measurement (M5) can name the differ it
measured.
"""

from __future__ import annotations

import ast
import copy
import io
import logging
import tokenize
from typing import Final

from provalume.schemas.freshness import (
    IRRELEVANT_REASON_CODES,
    ReasonCode,
    RelevanceVerdict,
)

log = logging.getLogger("provalume.freshness")

#: Bump on any change to classification semantics after verdicts have
#: shipped in a release.
DIFFER_VERSION: Final = "1"

#: Most-alarming-first, for per-record aggregation. ``BODY_CHANGED``
#: outranks ``IMPORT_CHANGED``: a deleted radius file reports as a body
#: change and must not be masked by an import edit elsewhere in the commit.
_REASON_PRIORITY: Final[tuple[ReasonCode, ...]] = (
    ReasonCode.UNPARSEABLE,
    ReasonCode.SIGNATURE_CHANGED,
    ReasonCode.BODY_CHANGED,
    ReasonCode.IMPORT_CHANGED,
    ReasonCode.DOCSTRING_ONLY,
    ReasonCode.COMMENT_ONLY,
    ReasonCode.WHITESPACE_ONLY,
)

#: Closed set of command markers whose presence means comments or docstrings
#: can change the command's outcome, so "trivia" classes stop being
#: irrelevant. Substring match, case-insensitive, deliberately dumb: a
#: deterministic filter must not grow a shell parser.
_COMMENT_SENSITIVE: Final[tuple[str, ...]] = (
    "doctest",
    "ruff",
    "flake8",
    "pylint",
    "pycodestyle",
    "pydocstyle",
    "mypy",
    "pyright",
    "bandit",
    "black",
    "isort",
    "lint",
)


def command_reads_trivia(command: str | None) -> bool:
    """Whether the verification command's outcome can depend on comments,
    docstrings, or formatting — in which case no change is trivia."""
    if not command:
        return False
    lowered = command.lower()
    return any(marker in lowered for marker in _COMMENT_SENSITIVE)


def strip_docstrings(tree: ast.Module) -> ast.Module:
    """A copy of ``tree`` with every docstring removed.

    A docstring is the first statement of a module, class, or function body
    when that statement is a string constant expression. Shared by the trivia
    stage (docstring-only detection) and the pipeline's AST backstop.
    """
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


def _comment_tokens(text: str) -> tuple[str, ...] | None:
    """The file's comment texts in order, or ``None`` when tokenization
    fails. Order matters: a moved comment is still a comment change."""
    try:
        return tuple(
            token.string
            for token in tokenize.generate_tokens(io.StringIO(text).readline)
            if token.type == tokenize.COMMENT
        )
    except Exception:
        return None


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

        # Lazy import: _trivia imports strip_docstrings from this module at
        # its own top level, so a module-level import here would be a cycle.
        from provalume.freshness import _trivia

        trivia = _trivia.classify(pre, post)
        if trivia is not None and trivia is not ReasonCode.DOCSTRING_ONLY:
            return _verdict(trivia)
        # DOCSTRING_ONLY falls through to the strip stage: its emptiness
        # guard is what distinguishes "the docstring changed" from "the
        # file's entire content was the docstring and it changed" — and the
        # structural stage backstops a trivia verdict the trees contradict.

        if ast.dump(pre_tree) == ast.dump(post_tree):
            # Comments and whitespace are not in the tree: the change cannot
            # be semantic, even when the trivia stage could not name it.
            # Label it by what actually differs — formatting unless the
            # comment tokens themselves changed.
            pre_comments = _comment_tokens(pre)
            post_comments = _comment_tokens(post)
            if pre_comments is None or post_comments is None or pre_comments != post_comments:
                return _verdict(ReasonCode.COMMENT_ONLY)
            return _verdict(ReasonCode.WHITESPACE_ONLY)
        stripped_pre = strip_docstrings(pre_tree)
        stripped_post = strip_docstrings(post_tree)
        if ast.dump(stripped_pre) == ast.dump(stripped_post):
            if not stripped_pre.body:
                # Stripping emptied the module on both sides while the raw
                # trees differ: the file's entire content was its docstring
                # and that content changed or vanished. "The docstring
                # changed" and "everything changed" are the same statement
                # here, and the alarming reading wins.
                return RelevanceVerdict.RELEVANT, ReasonCode.BODY_CHANGED
            return _verdict(ReasonCode.DOCSTRING_ONLY)

        from provalume.freshness import _structural

        return _verdict(_structural.classify(pre_tree, post_tree))
    except Exception:
        log.debug("relevance assessment failed; escalating", exc_info=True)
        return RelevanceVerdict.RELEVANT, ReasonCode.UNPARSEABLE


def assess_paths(
    files: list[tuple[str, str | None, str | None]],
    *,
    command: str | None = None,
) -> tuple[RelevanceVerdict, ReasonCode]:
    """A record's verdict over its intersecting files: relevant if ANY file
    escalates, reported with the most alarming reason seen. A command whose
    outcome reads comments or docstrings gets no irrelevant short-circuit."""
    if not files:
        return RelevanceVerdict.RELEVANT, ReasonCode.UNPARSEABLE
    reasons = [assess_file(pre, post)[1] for _path, pre, post in files]
    worst = min(reasons, key=_REASON_PRIORITY.index)
    verdict, reason = _verdict(worst)
    if verdict is RelevanceVerdict.IRRELEVANT and command_reads_trivia(command):
        # The reason code stays honest — the change IS comment-only — but
        # for this command comment-only is not harmless (LIMITATIONS §9e).
        return RelevanceVerdict.RELEVANT, reason
    return verdict, reason


def _verdict(reason: ReasonCode) -> tuple[RelevanceVerdict, ReasonCode]:
    if reason in IRRELEVANT_REASON_CODES:
        return RelevanceVerdict.IRRELEVANT, reason
    return RelevanceVerdict.RELEVANT, reason
