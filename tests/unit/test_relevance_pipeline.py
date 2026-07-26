"""The relevance pipeline's aggregation and escalation semantics.

The M3 review demonstrated that every behaviour standing between a semantic
change and a false ``current`` at the *record* level — any-escalates
aggregation, worst-reason selection, the empty-list escalation, the outer
fail-open — survived four independent inversions with the full suite green.
These tests are the regression pins those mutants proved missing. Each test
names the mutant it kills.
"""

from __future__ import annotations

from itertools import permutations

import pytest

from provalume.freshness import _trivia
from provalume.freshness.relevance import (
    _REASON_PRIORITY,
    DIFFER_VERSION,
    assess_file,
    assess_paths,
    command_reads_trivia,
)
from provalume.schemas.freshness import ReasonCode, RelevanceVerdict

TRIVIA = ("x = 1\n", "x = 1  # note\n")  # comment_only
SEMANTIC = ("x = 1\n", "x = 2\n")  # body_changed
SIGNATURE = ("def f(a):\n    pass\n", "def f(a, b):\n    pass\n")  # signature_changed


def test_one_escalating_file_escalates_the_record_in_any_order() -> None:
    """Any-escalates, order-independent (kills mutants E `min→max` and
    F `reasons[-1]`): one semantic change among trivia must decide the
    record, wherever it sits in the file list."""
    entries = [("a.py", *TRIVIA), ("b.py", *SEMANTIC), ("c.py", *TRIVIA)]
    for ordering in permutations(entries):
        assert assess_paths(list(ordering)) == (
            RelevanceVerdict.RELEVANT,
            ReasonCode.BODY_CHANGED,
        )


def test_all_trivia_aggregates_irrelevant_with_the_least_alarming_loser() -> None:
    """The dual control: when nothing escalates, the verdict is irrelevant —
    and the reported reason is still the most alarming of the trivia seen."""
    verdict, reason = assess_paths([("a.py", *TRIVIA), ("b.py", "x = 1\n", "x  =  1\n")])
    assert verdict is RelevanceVerdict.IRRELEVANT
    assert reason is ReasonCode.COMMENT_ONLY


def test_an_empty_file_list_escalates() -> None:
    """Kills mutant H: no files means the watcher had nothing to show the
    differ, and 'nothing to see' must never read as 'nothing happened'."""
    assert assess_paths([]) == (RelevanceVerdict.RELEVANT, ReasonCode.UNPARSEABLE)


def test_a_crash_inside_a_stage_escalates_not_clears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The outer fail-open (kills mutant J): an exploding classifier must
    surface as RELEVANT/UNPARSEABLE, never as a clean bill of health."""
    # Positive control first: without the fault this pair is trivially clean.
    assert assess_file(*TRIVIA) == (RelevanceVerdict.IRRELEVANT, ReasonCode.COMMENT_ONLY)

    def _explode(pre: str, post: str) -> ReasonCode | None:
        raise RuntimeError("injected: classifier crash")

    monkeypatch.setattr(_trivia, "classify", _explode)
    assert assess_file(*TRIVIA) == (RelevanceVerdict.RELEVANT, ReasonCode.UNPARSEABLE)
    assert assess_paths([("a.py", *TRIVIA)]) == (
        RelevanceVerdict.RELEVANT,
        ReasonCode.UNPARSEABLE,
    )


def test_priority_covers_every_reason_and_ranks_deletion_over_imports() -> None:
    """``BODY_CHANGED`` outranks ``IMPORT_CHANGED``: a deleted radius file
    reports as a body change and a sibling's import shuffle must not mask it
    (M3 review, finding 11)."""
    assert set(_REASON_PRIORITY) == set(ReasonCode)
    deleted = ("a.py", "x = 1\n", None)  # missing side → body_changed
    imports = ("b.py", "import os\nx = 1\n", "import sys\nx = 1\n")
    assert assess_paths([imports, deleted]) == (
        RelevanceVerdict.RELEVANT,
        ReasonCode.BODY_CHANGED,
    )
    assert _REASON_PRIORITY.index(ReasonCode.BODY_CHANGED) < _REASON_PRIORITY.index(
        ReasonCode.IMPORT_CHANGED
    )


# --- the command gate (M3 review, finding 4) ---------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "ruff check mod.py",
        "python -m pytest --doctest-modules mod.py",
        "mypy src/",
        "python -m bandit -r src",
        "make LINT=1 lint-all",
        "FLAKE8_CONFIG=x flake8 .",
        "coverage run -m pytest",
        "python -m pytest --cov=src --cov-fail-under=90",
        "interrogate -f 95 mod.py",
    ],
)
def test_comment_reading_commands_get_no_irrelevant_short_circuit(command: str) -> None:
    """A linter honours ``# noqa``, a doctest runner executes docstrings: for
    those commands a comment-only change can flip the outcome, so the trivia
    short-circuit is suppressed. The reason code stays honest — the change IS
    comment-only; it is the harmlessness that is withdrawn."""
    assert command_reads_trivia(command)
    assert assess_paths([("a.py", *TRIVIA)], command=command) == (
        RelevanceVerdict.RELEVANT,
        ReasonCode.COMMENT_ONLY,
    )


def test_a_plain_test_command_keeps_the_short_circuit() -> None:
    assert not command_reads_trivia("python -m pytest tests/")
    verdict, reason = assess_paths([("a.py", *TRIVIA)], command="python -m pytest tests/")
    assert verdict is RelevanceVerdict.IRRELEVANT
    assert reason is ReasonCode.COMMENT_ONLY


def test_the_gate_never_downgrades_a_relevant_verdict() -> None:
    assert assess_paths([("a.py", *SEMANTIC)], command="ruff check .") == (
        RelevanceVerdict.RELEVANT,
        ReasonCode.BODY_CHANGED,
    )


def test_no_command_means_no_gate() -> None:
    assert not command_reads_trivia(None)
    assert not command_reads_trivia("")


# --- backstop labelling (M3 review, finding 10) ------------------------------


def test_the_backstop_calls_formatting_whitespace_not_comments() -> None:
    """Dump-equal changes with identical comment tokens are formatting:
    reindentation, quote style, statement splitting. ``whitespace_only`` is
    the honest label; ``comment_only`` was a lie in the audit trail."""
    reindent = ("if x:\n    a()\n", "if x:\n  a()\n")
    split = ("a = 1; b = 2\n", "a = 1\nb = 2\n")
    quotes = ("s = 'x'\n", 's = "x"\n')
    for pre, post in (reindent, split, quotes):
        assert assess_file(pre, post) == (
            RelevanceVerdict.IRRELEVANT,
            ReasonCode.WHITESPACE_ONLY,
        )


def test_the_backstop_still_names_comment_changes_comment_only() -> None:
    """When the comment tokens DID change alongside unnameable formatting,
    the comment label is earned."""
    pre = "if x:\n    a()  # old note\n"
    post = "if x:\n  a()  # new note\n"
    assert assess_file(pre, post) == (RelevanceVerdict.IRRELEVANT, ReasonCode.COMMENT_ONLY)


# --- docstring stripping edge (M3 review, finding 13) ------------------------


def test_emptying_a_docstring_only_module_escalates() -> None:
    """A module whose entire content was its docstring: deleting or rewriting
    that docstring changes everything the file says, and both sides stripping
    to an empty tree must not read as a harmless docstring tweak."""
    assert assess_file('"""module doc"""\n', "") == (
        RelevanceVerdict.RELEVANT,
        ReasonCode.BODY_CHANGED,
    )
    assert assess_file('"""old doc"""\n', '"""new doc"""\n') == (
        RelevanceVerdict.RELEVANT,
        ReasonCode.BODY_CHANGED,
    )


def test_a_docstring_change_in_a_module_with_code_stays_docstring_only() -> None:
    pre = '"""old doc"""\nx = 1\n'
    post = '"""new doc"""\nx = 1\n'
    assert assess_file(pre, post) == (RelevanceVerdict.IRRELEVANT, ReasonCode.DOCSTRING_ONLY)


# --- version pin -------------------------------------------------------------


def test_differ_version_is_pinned() -> None:
    """M5 attributes every verdict to the differ that produced it. This pin
    fails when semantics change without the deliberate decision to bump (or
    hold, pre-release) recorded in DECISIONS.md."""
    assert DIFFER_VERSION == "1"
