"""The M5 corpus: cases for measuring invalidation precision.

Each case is one small repository, one verified record, one landed commit.
The taxonomy is drawn from the DOMAIN — kinds of changes developers land —
deliberately not from the relevance filter's code paths, because a corpus
generated from the implementation would grade what it already handles
(session DECISIONS D17). Categories:

  formatting | comments | docstrings | logic | signature | imports |
  structure (delete/rename/move) | strings | out-of-radius | mixed |
  broken-state

``{python}`` in a command is replaced with the harness interpreter.
``files_after`` maps path -> new content, or ``None`` to delete the path.
Every seed state must actually pass its command — the harness verifies
that before recording, so a corpus bug cannot masquerade as a measurement.

No case carries an expected outcome. Ground truth comes from the
independent labeling protocol in README.md, never from this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Case:
    case_id: str
    category: str
    command: str
    files_before: dict[str, str]
    files_after: dict[str, str | None] = field(default_factory=dict)


_CHECK_V1 = "import mod\nassert mod.compute() == 3\n"
_MOD_V1 = (
    "def helper(x):\n"
    "    # doubles the input\n"
    "    return x * 2\n"
    "\n"
    "\n"
    "def compute():\n"
    '    """Return the answer used by the pipeline."""\n'
    "    return helper(1) + 1\n"
)


def _case(
    case_id: str,
    category: str,
    after: dict[str, str | None],
    *,
    command: str = "{python} check.py",
    before: dict[str, str] | None = None,
) -> Case:
    files_before = {"mod.py": _MOD_V1, "check.py": _CHECK_V1}
    if before:
        files_before.update(before)
    return Case(
        case_id=case_id,
        category=category,
        command=command,
        files_before=files_before,
        files_after=after,
    )


CASES: tuple[Case, ...] = (
    # -- formatting ----------------------------------------------------------
    _case("fmt_blank_lines", "formatting", {"mod.py": _MOD_V1.replace("\n\n\n", "\n\n")}),
    _case(
        "fmt_reindent_two_spaces",
        "formatting",
        {
            "mod.py": "def helper(x):\n  # doubles the input\n  return x * 2\n\n\n"
            'def compute():\n  """Return the answer used by the pipeline."""\n'
            "  return helper(1) + 1\n"
        },
    ),
    _case(
        "fmt_trailing_whitespace",
        "formatting",
        {"mod.py": _MOD_V1.replace("return x * 2\n", "return x * 2   \n")},
    ),
    _case(
        "fmt_statement_split",
        "formatting",
        {"mod.py": _MOD_V1.replace("return helper(1) + 1", "value = helper(1) + 1;  return value")},
    ),
    # -- comments ------------------------------------------------------------
    _case(
        "cmt_reworded",
        "comments",
        {"mod.py": _MOD_V1.replace("# doubles the input", "# multiplies the input by two")},
    ),
    _case(
        "cmt_removed",
        "comments",
        {"mod.py": _MOD_V1.replace("    # doubles the input\n", "")},
    ),
    _case(
        "cmt_todo_added",
        "comments",
        {"mod.py": _MOD_V1.replace("return x * 2", "return x * 2  # TODO: rename")},
    ),
    # -- docstrings ----------------------------------------------------------
    _case(
        "doc_reworded",
        "docstrings",
        {
            "mod.py": _MOD_V1.replace(
                "Return the answer used by the pipeline.", "Compute the pipeline's answer."
            )
        },
    ),
    _case(
        "doc_removed",
        "docstrings",
        {"mod.py": _MOD_V1.replace('    """Return the answer used by the pipeline."""\n', "")},
    ),
    _case(
        "doc_doctest_broken",
        "docstrings",
        {
            "mod.py": "def double(x):\n"
            '    """Double x.\n\n    >>> double(2)\n    5\n    """\n'
            "    return x * 2\n"
        },
        command="{python} -m doctest mod.py",
        before={
            "mod.py": "def double(x):\n"
            '    """Double x.\n\n    >>> double(2)\n    4\n    """\n'
            "    return x * 2\n",
            "check.py": "",
        },
    ),
    # -- logic ---------------------------------------------------------------
    _case(
        "logic_breaks_claim",
        "logic",
        {"mod.py": _MOD_V1.replace("return x * 2", "return x * 3")},
    ),
    _case(
        "logic_equivalent_rewrite",
        "logic",
        {"mod.py": _MOD_V1.replace("return x * 2", "return x + x")},
    ),
    _case(
        "logic_unrelated_function",
        "logic",
        {
            "mod.py": _MOD_V1 + "\n\ndef unrelated():\n    return 99\n",
        },
    ),
    _case(
        "logic_constant_bumped",
        "logic",
        {"mod.py": _MOD_V1.replace("return helper(1) + 1", "return helper(1) + 2")},
    ),
    _case(
        "logic_dead_branch_added",
        "logic",
        {
            "mod.py": _MOD_V1.replace(
                "    return helper(1) + 1",
                "    if False:\n        return -1\n    return helper(1) + 1",
            )
        },
    ),
    _case(
        "logic_error_message_changed",
        "logic",
        {
            "mod.py": "def compute():\n"
            "    if 1 > 2:\n        raise ValueError('cannot happen anymore')\n"
            "    return 3\n",
        },
        before={
            "mod.py": "def compute():\n"
            "    if 1 > 2:\n        raise ValueError('cannot happen')\n"
            "    return 3\n",
            "check.py": _CHECK_V1,
        },
    ),
    # -- signature -----------------------------------------------------------
    _case(
        "sig_param_added_with_default",
        "signature",
        {
            "mod.py": _MOD_V1.replace("def helper(x):", "def helper(x, scale=2):").replace(
                "return x * 2", "return x * scale"
            )
        },
    ),
    _case(
        "sig_param_added_breaking",
        "signature",
        {
            "mod.py": _MOD_V1.replace("def helper(x):", "def helper(x, scale):").replace(
                "return x * 2", "return x * scale"
            )
        },
    ),
    _case(
        "sig_renamed_function",
        "signature",
        {
            "mod.py": _MOD_V1.replace("def compute():", "def calculate():"),
        },
    ),
    # -- imports -------------------------------------------------------------
    _case(
        "imp_unused_added",
        "imports",
        {"mod.py": "import os\n\n" + _MOD_V1},
    ),
    _case(
        "imp_stdlib_now_used",
        "imports",
        {
            "mod.py": "import math\n\n"
            + _MOD_V1.replace("return helper(1) + 1", "return int(math.floor(helper(1) + 1.2))")
        },
    ),
    _case(
        "imp_helper_moved_out",
        "imports",
        {
            "mod.py": "from helpers import helper\n\n\ndef compute():\n"
            '    """Return the answer used by the pipeline."""\n'
            "    return helper(1) + 1\n",
            "helpers.py": "def helper(x):\n    # doubles the input\n    return x * 2\n",
        },
    ),
    # -- structure -----------------------------------------------------------
    _case("struct_module_deleted", "structure", {"mod.py": None}),
    _case(
        "struct_renamed_import_fixed",
        "structure",
        {
            "mod.py": None,
            "core.py": _MOD_V1,
            "check.py": _CHECK_V1.replace("import mod", "import core as mod"),
        },
    ),
    _case(
        "struct_check_tightened",
        "structure",
        {"check.py": "import mod\nassert mod.compute() == 3\nassert mod.helper(0) == 0\n"},
    ),
    _case(
        "struct_check_now_fails",
        "structure",
        {"check.py": "import mod\nassert mod.compute() == 4\n"},
    ),
    # -- strings -------------------------------------------------------------
    _case(
        "str_asserted_value_changed",
        "strings",
        {
            "mod.py": "def greet():\n    return 'hi there'\n",
            "check.py": "import mod\nassert mod.greet() == 'hi there'\n",
        },
        before={
            "mod.py": "def greet():\n    return 'hi'\n",
            "check.py": "import mod\nassert mod.greet() == 'hi'\n",
        },
    ),
    _case(
        "str_whitespace_inside_string",
        "strings",
        {"mod.py": "def greet():\n    return 'hi  there'\n"},
        before={
            "mod.py": "def greet():\n    return 'hi there'\n",
            "check.py": "import mod\nassert mod.greet().startswith('hi')\n",
        },
        command="{python} check.py",
    ),
    _case(
        "str_quote_style_only",
        "strings",
        {"mod.py": _MOD_V1 + '\nLABEL = "x"\n'},
        before={"mod.py": _MOD_V1 + "\nLABEL = 'x'\n", "check.py": _CHECK_V1},
    ),
    # -- out-of-radius -------------------------------------------------------
    _case("oor_readme_only", "out-of-radius", {"README.md": "docs changed\n"}),
    _case(
        "oor_unimported_module",
        "out-of-radius",
        {"other.py": "def nothing():\n    return None\n"},
    ),
    # -- mixed ---------------------------------------------------------------
    _case(
        "mix_comment_and_break",
        "mixed",
        {
            "mod.py": _MOD_V1.replace("# doubles the input", "# triples the input").replace(
                "return x * 2", "return x * 3"
            )
        },
    ),
    _case(
        "mix_docstring_and_harmless",
        "mixed",
        {
            "mod.py": _MOD_V1.replace(
                "Return the answer used by the pipeline.", "The pipeline's answer."
            )
            + "\n\ndef extra():\n    return 0\n"
        },
    ),
    _case(
        "mix_format_two_files_one_break",
        "mixed",
        {
            "mod.py": _MOD_V1.replace("\n\n\n", "\n\n"),
            "check.py": "import mod\nassert mod.compute() == 30\n",
        },
    ),
    # -- broken-state --------------------------------------------------------
    _case(
        "broken_syntax_error_landed",
        "broken-state",
        {"mod.py": "def helper(x:\n    return x * 2\n"},
    ),
    _case(
        "broken_type_comment_removed",
        "broken-state",
        {"mod.py": _MOD_V1.replace("def helper(x):", "def helper(x):  # type: ignore")},
    ),
    _case(
        "broken_encoding_cookie",
        "broken-state",
        {"mod.py": "# -*- coding: latin-1 -*-\n" + _MOD_V1},
    ),
)
