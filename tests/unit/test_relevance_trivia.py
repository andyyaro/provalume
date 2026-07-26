"""The trivia classifiers: whitespace-only, comment-only, docstring-only.

These three reason codes are the only ones that may leave a record ``current``
after a landed commit touched its blast radius (``IRRELEVANT_REASON_CODES``),
so this is the one module in the freshness engine where a wrong answer is
worse than no answer. The asymmetry is the whole design (spec §5.3): a wrong
``None`` costs one re-run, a wrong reason code serves a stale fact as
verified. Every test here is written from that bias — the escalating direction
is cheap, the trivia direction must be earned.

Three properties the suite pins deliberately:

- **Indentation is semantics, not formatting.** Python's blocks *are* its
  whitespace. A statement dedented out of an ``if`` must never read as a
  whitespace change, and ``test_a_statement_leaving_its_block_escalates`` is
  the test that matters most in this file.
- **No trivia answer may contradict the AST.** Every pair asserted trivia is
  re-checked against ``ast.dump`` (raw for whitespace and comments, after
  ``strip_docstrings`` for docstrings), and against the pipeline that consumes
  the answer. ``test_token_equal_but_tree_different_escalates`` shows a real
  source pair where the token stream alone would have said whitespace-only and
  been catastrophically wrong.
- **Non-docstring strings are not docstrings.** A string assigned to a
  variable, a string that is not a body's first statement, an f-string, a
  triple-quoted blob used as data — each is a false-trivia trap, and each
  escalates.

Pairs are plain source strings: the unit under test takes text, so a temporary
file would only add a filesystem to the test.
"""

from __future__ import annotations

import ast
import tokenize

import pytest

from provalume.freshness._trivia import _token_pairs, _without_comments, classify
from provalume.freshness.relevance import assess_file, strip_docstrings
from provalume.schemas.freshness import ReasonCode, RelevanceVerdict

# --- helpers -----------------------------------------------------------------


def _dump(source: str) -> str:
    return ast.dump(ast.parse(source))


def _stripped_dump(source: str) -> str:
    return ast.dump(strip_docstrings(ast.parse(source)))


def assert_trivia(pre: str, post: str, expected: ReasonCode) -> None:
    """One trivia pair, in both directions, cross-checked three ways.

    The classification itself, the AST relationship the reason code claims
    (whitespace and comments cannot move the tree at all; a docstring edit
    moves it only until the docstrings are stripped), and the pipeline verdict
    the reason code produces downstream. A classifier that satisfies the first
    but not the second is the dangerous kind of wrong.
    """
    for before, after in ((pre, post), (post, pre)):
        assert classify(before, after) is expected
        if expected is ReasonCode.DOCSTRING_ONLY:
            assert _dump(before) != _dump(after), "a docstring-only claim needs a raw difference"
            assert _stripped_dump(before) == _stripped_dump(after)
        else:
            assert _dump(before) == _dump(after), "this reason code claims the tree cannot move"
        assert assess_file(before, after) == (RelevanceVerdict.IRRELEVANT, expected)


def assert_escalates(
    pre: str,
    post: str,
    *,
    pipeline: RelevanceVerdict = RelevanceVerdict.RELEVANT,
) -> None:
    """One pair the trivia stage must refuse to name, in both directions.

    Only the pipeline *verdict* is asserted, never its reason code: naming the
    escalation is the structural stage's job and its vocabulary is not this
    unit's contract.
    """
    for before, after in ((pre, post), (post, pre)):
        assert classify(before, after) is None
        assert assess_file(before, after)[0] is pipeline


# --- the reduced token stream ------------------------------------------------


def test_reduced_stream_drops_blank_lines_and_keeps_block_structure() -> None:
    """The comparison unit itself: ``(type, text)`` pairs with no positions,
    blank-line ``NL`` dropped, and ``INDENT``/``DEDENT``/``NEWLINE`` kept —
    because positions move under every whitespace edit (which would make the
    whitespace rule unreachable) while block structure is semantics."""
    pairs = _token_pairs("if x:\n\n    a()\n")
    assert pairs is not None
    assert all(len(pair) == 2 for pair in pairs), "positions must not be compared"
    kinds = [tokenize.tok_name[kind] for kind, _text in pairs]
    assert "NL" not in kinds, "blank lines are the one thing ignored"
    assert kinds.count("INDENT") == 1
    assert kinds.count("DEDENT") == 1
    assert kinds.count("NEWLINE") == 2


def test_newline_text_is_canonicalised_but_the_token_survives() -> None:
    """A ``NEWLINE`` token's text is a line terminator and nothing else: LF,
    CRLF, or empty at end-of-file. Comparing that text would make "added a
    trailing newline" a semantic change; dropping the token would make a
    statement boundary invisible. So the text goes and the token stays."""
    with_newline = _token_pairs("x = 1\n")
    without_newline = _token_pairs("x = 1")
    assert with_newline is not None
    assert with_newline == without_newline
    assert tokenize.NEWLINE in [kind for kind, _text in with_newline]
    assert all(text == "" for kind, text in with_newline if kind == tokenize.NEWLINE)


# --- whitespace-only ---------------------------------------------------------

WHITESPACE_PAIRS = {
    "blank_line_inserted": ("x = 1\ny = 2\n", "x = 1\n\ny = 2\n"),
    "blank_lines_removed": ("def f():\n    a()\n\n\n    b()\n", "def f():\n    a()\n    b()\n"),
    "trailing_spaces_added": ("x = 1\ny = 2\n", "x = 1   \ny = 2\t\n"),
    "intra_line_spacing": ("x=1\n", "x = 1\n"),
    "spacing_in_a_call": ("f( a,b )\n", "f(a, b)\n"),
    "spacing_inside_an_fstring_slot": ('s = f"{a}"\n', 's = f"{ a }"\n'),
    "trailing_newline_added": ("x = 1", "x = 1\n"),
    "trailing_newline_added_inside_a_block": (
        "def f():\n    return 1",
        "def f():\n    return 1\n",
    ),
    "blank_line_at_eof": ("x = 1\n", "x = 1\n\n\n"),
    "line_endings_converted_to_crlf": ("if x:\n    a()\n", "if x:\r\n    a()\r\n"),
    "continuation_reflowed": ("z = (a +\n     b)\n", "z = (\n    a\n    + b\n)\n"),
}


@pytest.mark.parametrize("case", sorted(WHITESPACE_PAIRS))
def test_whitespace_only_changes_are_named_whitespace(case: str) -> None:
    """Everything that never reaches a token's text, plus blank lines and the
    trailing-newline artifact. ``x=1`` versus ``x = 1`` is the reminder that
    intra-line spacing is invisible to the tokenizer to begin with."""
    pre, post = WHITESPACE_PAIRS[case]
    assert_trivia(pre, post, ReasonCode.WHITESPACE_ONLY)


def test_an_unchanged_file_is_whitespace_only() -> None:
    """A degenerate input the pipeline should never produce (git does not
    report unchanged files), pinned because it must not surprise: no change at
    all cannot be a semantic change, so the safe short-circuit is correct."""
    assert_trivia("x = 1\n", "x = 1\n", ReasonCode.WHITESPACE_ONLY)


# --- indentation is semantics ------------------------------------------------


BLOCK_MOVES = {
    "dedented_out_of_an_if": (
        "if ready:\n    launch()\n    notify()\n",
        "if ready:\n    launch()\nnotify()\n",
    ),
    "dedented_out_of_a_loop_body": (
        "for row in rows:\n    send(row)\n    commit()\n",
        "for row in rows:\n    send(row)\ncommit()\n",
    ),
    "moved_from_a_try_body_into_its_handler": (
        "try:\n    fetch()\n    parse()\nexcept OSError:\n    report()\n",
        "try:\n    fetch()\nexcept OSError:\n    parse()\n    report()\n",
    ),
    "else_branch_reattached_to_the_outer_statement": (
        "for row in rows:\n    if row:\n        keep(row)\n    else:\n        drop(row)\n",
        "for row in rows:\n    if row:\n        keep(row)\nelse:\n    drop(row)\n",
    ),
    "moved_out_of_a_with_block": (
        "with lock:\n    read()\n    write()\n",
        "with lock:\n    read()\nwrite()\n",
    ),
}


@pytest.mark.parametrize("case", sorted(BLOCK_MOVES))
def test_a_statement_leaving_its_block_escalates(case: str) -> None:
    """The tests that matter most in this unit.

    A line dedented out of an ``if`` is a whitespace edit in a text editor's
    sense and a control-flow edit in Python's; the same edit can move a
    statement from a ``try`` body into the handler meant to catch it, or
    reattach an ``else`` to a different statement entirely. Every line in every
    pair below tokenizes identically — only the ``INDENT``/``DEDENT`` positions
    differ, which is why they are not in the ignored set.

    Both guards are asserted separately and on purpose: the reduced token
    streams must already differ (so the answer does not lean on the AST
    cross-check), and the trees must differ too (so the cross-check would
    catch it even if the stream comparison were later loosened). Defence in
    depth is only defence if each layer is tested alone.
    """
    pre, post = BLOCK_MOVES[case]
    assert _token_pairs(pre) != _token_pairs(post), "INDENT/DEDENT must not be ignored"
    assert _dump(pre) != _dump(post), "the change really is semantic"
    assert_escalates(pre, post)


REINDENT_PAIRS = {
    "four_spaces_to_two": ("if x:\n    a()\n", "if x:\n  a()\n"),
    "spaces_to_tabs": ("if x:\n    a()\n", "if x:\n\ta()\n"),
}


@pytest.mark.parametrize("case", sorted(REINDENT_PAIRS))
def test_reindentation_width_escalates_to_the_backstop(case: str) -> None:
    """A deliberate conservatism, recorded so it is not mistaken for a bug.

    ``INDENT`` carries the literal indentation text, so re-indenting a block
    from four spaces to two (or to a tab) is not stream-identical and this
    stage returns ``None`` rather than reinterpreting a frozen contract that
    says "identical ignoring only ``NL``". The change is still not re-run: the
    pipeline's AST backstop catches it — and labels it ``WHITESPACE_ONLY``,
    because no comment token differs and calling a reindent a comment change
    would degrade the audit trail the closed enum exists for.
    """
    pre, post = REINDENT_PAIRS[case]
    assert_escalates(pre, post, pipeline=RelevanceVerdict.IRRELEVANT)
    assert _dump(pre) == _dump(post)
    assert assess_file(pre, post) == (RelevanceVerdict.IRRELEVANT, ReasonCode.WHITESPACE_ONLY)


# --- comment-only ------------------------------------------------------------

COMMENT_PAIRS = {
    "line_comment_added": ("x = 1\n", "# why one\nx = 1\n"),
    "line_comment_removed": ("# why one\nx = 1\n", "x = 1\n"),
    "line_comment_edited": ("# why one\nx = 1\n", "# why exactly one\nx = 1\n"),
    "inline_comment_added": ("x = 1\n", "x = 1  # why\n"),
    "inline_comment_edited": ("x = 1  # why\n", "x = 1  # because\n"),
    "inline_comment_removed": ("x = 1  # why\n", "x = 1\n"),
    "comment_inside_a_block": (
        "def f():\n    return 1\n",
        "def f():\n    # the only sane value\n    return 1\n",
    ),
    "comment_at_eof_without_newline": ("x = 1\n", "x = 1\n# trailing thought"),
    "noqa_marker_added": ("import os\n", "import os  # noqa: F401\n"),
    "commented_out_code_added": ("keep()\n", "keep()\n# drop()\n"),
}


@pytest.mark.parametrize("case", sorted(COMMENT_PAIRS))
def test_comment_only_changes_are_named_comment(case: str) -> None:
    """Comments reach the token stream (unlike spacing) but never the tree, so
    they get their own reason code rather than borrowing whitespace's."""
    pre, post = COMMENT_PAIRS[case]
    assert_trivia(pre, post, ReasonCode.COMMENT_ONLY)


def test_comment_and_blank_line_changes_together_are_comment_only() -> None:
    """The realistic edit — a comment added and the surrounding blank lines
    reflowed with it. Comment-only outranks whitespace-only when both moved,
    because the comment difference is the one that survives the more
    permissive comparison."""
    assert_trivia(
        "def f():\n    a()\n    b()\n",
        "def f():\n    # setup\n    a()\n\n    # work\n    b()\n",
        ReasonCode.COMMENT_ONLY,
    )


def test_a_comment_edit_is_comment_only_not_whitespace_only() -> None:
    """The two irrelevant codes must stay distinguishable: whitespace-only is
    the stricter comparison and must not absorb comment edits, or M5 could not
    measure the classes apart."""
    assert classify("x = 1\n", "x = 1  # note\n") is ReasonCode.COMMENT_ONLY
    assert classify("x = 1\n", "x = 1\n\n") is ReasonCode.WHITESPACE_ONLY


# --- docstring-only ----------------------------------------------------------

DOCSTRING_PAIRS = {
    "module_docstring_edited": ('"""Old summary."""\n\nx = 1\n', '"""New summary."""\n\nx = 1\n'),
    "function_docstring_edited": (
        'def f():\n    """Old."""\n    return 1\n',
        'def f():\n    """New, with detail."""\n    return 1\n',
    ),
    "class_docstring_edited": (
        'class C:\n    """Old."""\n\n    x = 1\n',
        'class C:\n    """New."""\n\n    x = 1\n',
    ),
    "nested_method_docstring_edited": (
        'class C:\n    def m(self):\n        """Old."""\n        return 1\n',
        'class C:\n    def m(self):\n        """New."""\n        return 1\n',
    ),
    "async_function_docstring_edited": (
        'async def f():\n    """Old."""\n    await g()\n',
        'async def f():\n    """New."""\n    await g()\n',
    ),
    "docstring_is_the_whole_body": ('def f():\n    """Old."""\n', 'def f():\n    """New."""\n'),
    "module_docstring_added": ("x = 1\n", '"""Now documented."""\n\nx = 1\n'),
    "function_docstring_added": (
        "def f():\n    return 1\n",
        'def f():\n    """Now documented."""\n    return 1\n',
    ),
    "class_docstring_removed": (
        'class C:\n    """Gone."""\n\n    x = 1\n',
        "class C:\n    x = 1\n",
    ),
    "docstring_reflowed_to_multiline": (
        'def f():\n    """One line."""\n    return 1\n',
        'def f():\n    """One line.\n\n    Now with a body.\n    """\n    return 1\n',
    ),
}


@pytest.mark.parametrize("case", sorted(DOCSTRING_PAIRS))
def test_docstring_only_changes_are_named_docstring(case: str) -> None:
    """Docstring text is in the tree, so this rule cannot be a token
    comparison: it is AST equality after ``strip_docstrings`` with raw
    inequality, which is exactly what the helper re-asserts."""
    pre, post = DOCSTRING_PAIRS[case]
    assert_trivia(pre, post, ReasonCode.DOCSTRING_ONLY)


def test_adding_or_removing_a_docstring_entirely_is_docstring_only() -> None:
    """Decided against the contract rather than by intuition.

    Stripping removes a body's leading string constant whether or not the
    other side has one, so "docstring added where none existed" and "docstring
    deleted" both land on stripped-equal-raw-different: ``DOCSTRING_ONLY``.
    That is right on the merits too — documentation appearing or disappearing
    cannot change what the code does — and it is the same class the pipeline
    treats as irrelevant either way.
    """
    undocumented = "def f(a):\n    return a + 1\n"
    documented = 'def f(a):\n    """Add one."""\n    return a + 1\n'
    assert classify(undocumented, documented) is ReasonCode.DOCSTRING_ONLY
    assert classify(documented, undocumented) is ReasonCode.DOCSTRING_ONLY
    assert _stripped_dump(undocumented) == _stripped_dump(documented)
    assert _dump(undocumented) != _dump(documented)


def test_a_docstring_edit_plus_a_comment_edit_is_docstring_only() -> None:
    """Reasoned through, because the rules are ordered and this pair reaches
    the third one.

    The streams differ beyond comments (the docstring text is a ``STRING``
    token), so rules one and two both decline. The stripped trees are equal
    and the raw trees are not, so rule three fires: ``DOCSTRING_ONLY``. That
    is the pipeline-intended answer — both classes are irrelevant, and the
    reported reason is the more alarming of the two, which is the same
    convention ``assess_paths`` uses when aggregating.
    """
    pre = 'def f():\n    """Old."""\n    # old note\n    return 1\n'
    post = 'def f():\n    """New."""\n    # new note\n    return 1\n'
    assert_trivia(pre, post, ReasonCode.DOCSTRING_ONLY)


def test_a_docstring_edit_plus_whitespace_is_docstring_only() -> None:
    assert_trivia(
        '"""Old."""\nx = 1\ny = 2\n',
        '"""New."""\n\nx = 1\n\ny = 2\n',
        ReasonCode.DOCSTRING_ONLY,
    )


def test_a_docstring_requoted_without_changing_its_value_escalates_to_the_backstop() -> None:
    """Rule three requires the raw trees to differ, and re-quoting a docstring
    leaves them identical — the tokens changed, the value did not. The stage
    declines and the pipeline's backstop names it, exactly as the contract
    routes it."""
    pre = 'def f():\n    """Same."""\n    return 1\n'
    post = "def f():\n    'Same.'\n    return 1\n"
    assert_escalates(pre, post, pipeline=RelevanceVerdict.IRRELEVANT)
    assert _dump(pre) == _dump(post)


# --- real changes escalate ---------------------------------------------------

REAL_CHANGES = {
    "operator_changed": ("z = a + b\n", "z = a - b\n"),
    "literal_changed": ("TIMEOUT = 30\n", "TIMEOUT = 60\n"),
    "comparison_flipped": ("if a < b:\n    go()\n", "if a <= b:\n    go()\n"),
    "variable_renamed": (
        "def f(items):\n    return len(items)\n",
        "def f(rows):\n    return len(rows)\n",
    ),
    "statement_added": ("def f():\n    return 1\n", "def f():\n    audit()\n    return 1\n"),
    "statement_removed": ("def f():\n    audit()\n    return 1\n", "def f():\n    return 1\n"),
    "call_argument_added": ("connect(host)\n", "connect(host, retries=3)\n"),
    "import_changed": ("import os\n", "import os\nimport sys\n"),
    "decorator_added": ("def f():\n    return 1\n", "@cache\ndef f():\n    return 1\n"),
    "return_value_changed": ("def f():\n    return 1\n", "def f():\n    return 2\n"),
    "keyword_negated": ("if ok:\n    go()\n", "if not ok:\n    go()\n"),
}


@pytest.mark.parametrize("case", sorted(REAL_CHANGES))
def test_real_code_changes_escalate(case: str) -> None:
    pre, post = REAL_CHANGES[case]
    assert_escalates(pre, post)


STRING_TRAPS = {
    "string_assigned_to_a_variable": ('BANNER = "hello"\n', 'BANNER = "goodbye"\n'),
    "string_expression_that_is_not_first": ('x = 1\n"not a docstring"\n', 'x = 1\n"changed"\n'),
    "string_after_a_statement_in_a_function": (
        'def f():\n    x = 1\n    "not a docstring"\n',
        'def f():\n    x = 1\n    "changed"\n',
    ),
    "string_first_inside_an_if_block": ('if x:\n    "not a docstring"\n', 'if x:\n    "changed"\n'),
    "second_string_after_a_real_docstring": (
        'def f():\n    """Doc."""\n    "payload"\n',
        'def f():\n    """Doc."""\n    "changed"\n',
    ),
    "triple_quoted_data_blob": (
        'SQL = """\nSELECT 1\n"""\n',
        'SQL = """\nSELECT 2\n"""\n',
    ),
    "docstring_turned_into_an_assignment": (
        'def f():\n    """Doc."""\n',
        'def f():\n    note = "Doc."\n',
    ),
    "string_returned_not_documented": (
        'def f():\n    return "ok"\n',
        'def f():\n    return "OK"\n',
    ),
}


@pytest.mark.parametrize("case", sorted(STRING_TRAPS))
def test_strings_that_are_not_docstrings_escalate(case: str) -> None:
    """The classic false-trivia traps. Every one of these *looks* like prose
    and none of them is a docstring: ``strip_docstrings`` removes only a
    leading string constant in a module, class, or function body, so each of
    these differences survives stripping and escalates."""
    pre, post = STRING_TRAPS[case]
    assert_escalates(pre, post)


FSTRING_CHANGES = {
    "fstring_literal_text": (
        'def f(a):\n    return f"v={a}"\n',
        'def f(a):\n    return f"val={a}"\n',
    ),
    "fstring_expression": (
        'def f(a, b):\n    return f"{a}"\n',
        'def f(a, b):\n    return f"{b}"\n',
    ),
    "fstring_format_spec": (
        'def f(a):\n    return f"{a:.2f}"\n',
        'def f(a):\n    return f"{a:.3f}"\n',
    ),
    "fstring_as_leading_expression": ('def f(a):\n    f"{a}"\n', 'def f(a):\n    f"{a!r}"\n'),
}


@pytest.mark.parametrize("case", sorted(FSTRING_CHANGES))
def test_fstring_content_changes_escalate(case: str) -> None:
    """An f-string is a ``JoinedStr``, never a docstring — including as a
    body's first statement, which is why the last case matters."""
    pre, post = FSTRING_CHANGES[case]
    assert_escalates(pre, post)


# --- the AST cross-check is load-bearing -------------------------------------

# Old-Mac line endings: ``ast.parse`` treats each lone ``\r`` as a line break,
# ``tokenize`` does not. The file collapses into one logical line whose
# indentation never reaches a token's text, so the two sides below — which
# differ only in whether ``bell()`` runs inside ``if hot:`` or outside it —
# tokenize identically. Not hypothetical: git will hand the differ whatever a
# commit contained.
CR_NESTED_PRE = "if warm:\r    if hot:\r        alarm()\r        bell()\r"
CR_NESTED_POST = "if warm:\r    if hot:\r        alarm()\r    bell()\r"


def test_token_equal_but_tree_different_escalates() -> None:
    """A token-stream-only classifier would call this pair whitespace-only and
    leave a control-flow change marked ``current``. The premise is asserted
    first so the test explains itself: the reduced streams really are equal,
    the trees really are not, and the classification is still ``None``."""
    assert _token_pairs(CR_NESTED_PRE) == _token_pairs(CR_NESTED_POST), (
        "premise: the token streams are indistinguishable"
    )
    assert _dump(CR_NESTED_PRE) != _dump(CR_NESTED_POST), "premise: the trees are not"
    assert_escalates(CR_NESTED_PRE, CR_NESTED_POST)


def test_comment_equal_but_tree_different_escalates() -> None:
    """The same hole under the second rule, which needs its own pair.

    Prefix the collapsing pair above with one ordinary LF-terminated comment
    line and edit that comment. Now the streams differ *only* in a ``COMMENT``
    token, so dropping comments makes them equal — and a comment-only claim
    would ship a moved statement as ``current``. The cross-check is what makes
    both rules safe, not just the first.
    """
    pre = "# alarm first\n" + CR_NESTED_PRE
    post = "# alarm first, then bell\n" + CR_NESTED_POST
    pre_tokens, post_tokens = _token_pairs(pre), _token_pairs(post)
    assert pre_tokens is not None and post_tokens is not None
    assert pre_tokens != post_tokens, "premise: the comment differs"
    assert _without_comments(pre_tokens) == _without_comments(post_tokens), (
        "premise: dropping comments makes the streams identical"
    )
    assert _dump(pre) != _dump(post), "premise: the trees differ anyway"
    assert_escalates(pre, post)


def test_cr_line_endings_still_classify_when_nothing_moved() -> None:
    """The cross-check is a veto, not a blanket refusal: exotic line endings
    with a genuinely identical tree still classify."""
    assert_trivia("x = 1\ry = 2\r", "x = 1\ry = 2\r", ReasonCode.WHITESPACE_ONLY)


# --- never raises ------------------------------------------------------------

WHITESPACE_CONTROL = ("x = 1\ny = 2\n", "x = 1\n\ny = 2\n")


def test_a_tokenize_failure_escalates_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """A file can parse and still upset ``tokenize``. The positive control runs
    first: without the fault the same pair is whitespace-only, so this cannot
    pass by never doing anything."""
    pre, post = WHITESPACE_CONTROL
    assert classify(pre, post) is ReasonCode.WHITESPACE_ONLY

    def refuse(*args: object, **kwargs: object) -> object:
        raise tokenize.TokenError("injected: unexpected EOF in multi-line statement")

    monkeypatch.setattr(tokenize, "generate_tokens", refuse)
    assert classify(pre, post) is None


def test_an_unexpected_internal_failure_escalates_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The blanket guarantee, exercised with an exception type no narrow
    handler anticipates: fail open (I5), never propagate."""
    pre, post = WHITESPACE_CONTROL

    def explode(*args: object, **kwargs: object) -> object:
        raise RuntimeError("injected: something no handler expects")

    monkeypatch.setattr(tokenize, "generate_tokens", explode)
    assert classify(pre, post) is None


def test_a_docstring_stage_failure_escalates_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same guarantee behind the third rule, where the work is AST rather
    than tokens. Positive control first."""
    pre = 'def f():\n    """Old."""\n    return 1\n'
    post = 'def f():\n    """New."""\n    return 1\n'
    assert classify(pre, post) is ReasonCode.DOCSTRING_ONLY

    def explode(*args: object, **kwargs: object) -> object:
        raise RecursionError("injected: tree too deep to strip")

    monkeypatch.setattr("provalume.freshness._trivia.strip_docstrings", explode)
    assert classify(pre, post) is None


UNPARSEABLE_INPUTS = [
    ("def f(:\n", "x = 1\n"),
    ("x = 1\n", "def f(:\n"),
    ("x = 1\\", "x = 1\n"),
    ("if x:\n", "if x:\n    pass\n"),
    ("class C:\n\tx = 1\n        y = 2\n", "class C:\n    x = 1\n"),
]


@pytest.mark.parametrize(("pre", "post"), UNPARSEABLE_INPUTS)
def test_unparseable_input_escalates_instead_of_raising(pre: str, post: str) -> None:
    """The pipeline screens for parseability before calling in, so these are
    contract violations by definition — and the answer to a contract violation
    is still ``None``, never a traceback out of a fail-open path. The last pair
    is the one that reaches ``tokenize`` and fails there (inconsistent tabs)
    rather than failing ``ast.parse`` first."""
    assert classify(pre, post) is None
    assert classify(post, pre) is None


def test_empty_versus_blank_file_is_whitespace_only() -> None:
    """Both parse to an empty module, so this is a real classification rather
    than a degenerate escape: a file emptied to blank lines changed nothing."""
    assert_trivia("", "\n\n\n", ReasonCode.WHITESPACE_ONLY)


# --- determinism -------------------------------------------------------------

ALL_PAIRS = [
    *WHITESPACE_PAIRS.values(),
    *BLOCK_MOVES.values(),
    *REINDENT_PAIRS.values(),
    *COMMENT_PAIRS.values(),
    *DOCSTRING_PAIRS.values(),
    *REAL_CHANGES.values(),
    *STRING_TRAPS.values(),
    *FSTRING_CHANGES.values(),
    (CR_NESTED_PRE, CR_NESTED_POST),
    ("# alarm first\n" + CR_NESTED_PRE, "# alarm first, then bell\n" + CR_NESTED_POST),
]


def test_classification_is_deterministic_and_input_preserving() -> None:
    """Same inputs, same answer, no accumulated state — a differ whose verdict
    depended on call order could not be measured (M5) or trusted (§5.3). Run
    over every pair in this file, twice, forwards and backwards."""
    first = [(classify(pre, post), classify(post, pre)) for pre, post in ALL_PAIRS]
    second = [(classify(pre, post), classify(post, pre)) for pre, post in ALL_PAIRS]
    assert first == second
    assert len(first) == len(ALL_PAIRS)


def test_every_reason_this_stage_returns_is_an_irrelevant_one() -> None:
    """The stage's whole purpose: it may only ever produce codes that are safe
    to short-circuit on. Anything escalating must arrive as ``None`` so the
    pipeline decides the name."""
    produced = {classify(pre, post) for pre, post in ALL_PAIRS}
    produced |= {classify(post, pre) for pre, post in ALL_PAIRS}
    assert produced <= {
        None,
        ReasonCode.WHITESPACE_ONLY,
        ReasonCode.COMMENT_ONLY,
        ReasonCode.DOCSTRING_ONLY,
    }
    assert produced >= {
        None,
        ReasonCode.WHITESPACE_ONLY,
        ReasonCode.COMMENT_ONLY,
        ReasonCode.DOCSTRING_ONLY,
    }, "positive control: the table must exercise all four outcomes"
