"""Structural classification: naming a semantic change (M3; spec §5.3).

Every case is a pure source pair fed through ``ast.parse`` — nothing here
touches the filesystem or git. The one exception is the PYTHONHASHSEED check,
which re-runs the classifier in fresh interpreters because "deterministic"
has to mean *across processes*, not merely twice in a row.

The pairs are also the executable record of the decisions the frozen contract
left open, each documented in ``_structural``'s module docstring: defaults are
signature, decorators are signature, lambdas are body, imports are keyed by
their enclosing scope, and both signatures and imports are compared as
multisets rather than sets.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

import provalume
from provalume.freshness import _structural
from provalume.freshness.relevance import strip_docstrings
from provalume.schemas.freshness import IRRELEVANT_REASON_CODES, ReasonCode

SIGNATURE = ReasonCode.SIGNATURE_CHANGED
IMPORT = ReasonCode.IMPORT_CHANGED
BODY = ReasonCode.BODY_CHANGED

#: (id, pre source, post source) triples.
Pair = tuple[str, str, str]


def named(pre: str, post: str) -> ReasonCode:
    """``classify`` over a source pair, under the pipeline's precondition.

    ``relevance.assess_file`` reaches this stage only once both sides parse
    and their docstring-stripped dumps differ, so the fixture asserts that
    precondition: a pair that is accidentally identical would otherwise pass
    every expectation for the wrong reason.
    """
    pre_tree = ast.parse(dedent(pre))
    post_tree = ast.parse(dedent(post))
    assert ast.dump(strip_docstrings(pre_tree)) != ast.dump(strip_docstrings(post_tree)), (
        "fixture bug: the pipeline only calls classify when the stripped trees differ"
    )
    return _structural.classify(pre_tree, post_tree)


def ids(pairs: list[Pair]) -> list[pytest.ParameterSet]:
    return [pytest.param(pre, post, id=case) for case, pre, post in pairs]


# --- 1. Signature changes -----------------------------------------------------

SIGNATURE_PAIRS: list[Pair] = [
    (
        "renamed-function",
        "def alpha(x):\n    return x\n",
        "def beta(x):\n    return x\n",
    ),
    (
        "added-function",
        "def alpha(x):\n    return x\n",
        "def alpha(x):\n    return x\n\n\ndef beta(y):\n    return y\n",
    ),
    (
        "removed-function",
        "def alpha(x):\n    return x\n\n\ndef beta(y):\n    return y\n",
        "def alpha(x):\n    return x\n",
    ),
    (
        "renamed-parameter",
        "def f(value):\n    return 1\n",
        "def f(other):\n    return 1\n",
    ),
    (
        "added-parameter-with-default",
        "def f(a):\n    return a\n",
        "def f(a, b=None):\n    return a\n",
    ),
    (
        "changed-default-value",
        "def f(a=1):\n    return a\n",
        "def f(a=2):\n    return a\n",
    ),
    (
        "changed-annotation",
        "def f(a: int) -> None:\n    return None\n",
        "def f(a: str) -> None:\n    return None\n",
    ),
    (
        "changed-return-annotation",
        "def f(a: int) -> int:\n    return a\n",
        "def f(a: int) -> str:\n    return a\n",
    ),
    (
        "annotation-added",
        "def f(a):\n    return a\n",
        "def f(a: int):\n    return a\n",
    ),
    (
        "sync-to-async",
        "def f(a):\n    return a\n",
        "async def f(a):\n    return a\n",
    ),
    (
        "decorator-added",
        "def f(a):\n    return a\n",
        "@cache\ndef f(a):\n    return a\n",
    ),
    (
        "decorator-removed",
        "@cache\ndef f(a):\n    return a\n",
        "def f(a):\n    return a\n",
    ),
    (
        "decorator-changed",
        "@cache\ndef f(a):\n    return a\n",
        "@lru_cache\ndef f(a):\n    return a\n",
    ),
    (
        "decorator-argument-changed",
        "@retry(times=1)\ndef f(a):\n    return a\n",
        "@retry(times=2)\ndef f(a):\n    return a\n",
    ),
    (
        "decorator-order-swapped",
        "@one\n@two\ndef f(a):\n    return a\n",
        "@two\n@one\ndef f(a):\n    return a\n",
    ),
    (
        "method-moved-between-classes",
        """
        class A:
            def f(self):
                return 1


        class B:
            pass
        """,
        """
        class A:
            pass


        class B:
            def f(self):
                return 1
        """,
    ),
    (
        "method-parameter-changed",
        """
        class A:
            def f(self, a):
                return a
        """,
        """
        class A:
            def f(self, a, b):
                return a
        """,
    ),
    (
        "async-method-added",
        """
        class A:
            def f(self):
                return 1
        """,
        """
        class A:
            def f(self):
                return 1

            async def g(self):
                return 2
        """,
    ),
    (
        "nested-function-renamed",
        """
        def outer():
            def inner():
                return 1

            return inner
        """,
        """
        def outer():
            def renamed():
                return 1

            return renamed
        """,
    ),
    (
        "class-base-changed",
        "class A(Base):\n    pass\n",
        "class A(Other):\n    pass\n",
    ),
    (
        "class-base-order-swapped",
        "class A(One, Two):\n    pass\n",
        "class A(Two, One):\n    pass\n",
    ),
    (
        "class-keyword-changed",
        "class A(Base, metaclass=Meta):\n    pass\n",
        "class A(Base, metaclass=Other):\n    pass\n",
    ),
    (
        "class-decorator-added",
        "class A:\n    pass\n",
        "@dataclass\nclass A:\n    pass\n",
    ),
    (
        "positional-only-marker-added",
        "def f(a, b):\n    return a\n",
        "def f(a, /, b):\n    return a\n",
    ),
    (
        "keyword-only-marker-added",
        "def f(a, b):\n    return a\n",
        "def f(a, *, b):\n    return a\n",
    ),
    (
        "vararg-renamed",
        "def f(*args):\n    return args\n",
        "def f(*items):\n    return items\n",
    ),
    (
        "vararg-removed-leaving-bare-star",
        "def f(*args, b=1):\n    return b\n",
        "def f(*, b=1):\n    return b\n",
    ),
    (
        "kwarg-renamed",
        "def f(**kwargs):\n    return kwargs\n",
        "def f(**options):\n    return options\n",
    ),
    (
        "keyword-only-default-changed",
        "def f(*, a=1):\n    return a\n",
        "def f(*, a=2):\n    return a\n",
    ),
    (
        "keyword-only-default-removed",
        "def f(*, a=1):\n    return a\n",
        "def f(*, a):\n    return a\n",
    ),
    (
        "type-parameter-bound-added",
        "def f[T](a: T) -> T:\n    return a\n",
        "def f[T: int](a: T) -> T:\n    return a\n",
    ),
    (
        "duplicate-definition-removed",
        "def f(x):\n    return x\n\n\ndef f(x):\n    return x\n",
        "def f(x):\n    return x\n",
    ),
    (
        "overload-annotation-changed",
        """
        @overload
        def f(x: int) -> int: ...
        @overload
        def f(x: str) -> str: ...
        def f(x):
            return x
        """,
        """
        @overload
        def f(x: int) -> int: ...
        @overload
        def f(x: bytes) -> bytes: ...
        def f(x):
            return x
        """,
    ),
    (
        "conditional-definition-signature-changed",
        """
        if TYPE_CHECKING:
            def f(a: int) -> None: ...
        """,
        """
        if TYPE_CHECKING:
            def f(a: str) -> None: ...
        """,
    ),
    (
        "definition-added-inside-try",
        """
        try:
            def f(a):
                return a
        except ImportError:
            pass
        """,
        """
        try:
            def f(a):
                return a

            def g(a):
                return a
        except ImportError:
            pass
        """,
    ),
    (
        "class-replaced-by-function-of-the-same-name",
        "class A:\n    pass\n",
        "def A():\n    pass\n",
    ),
]


@pytest.mark.parametrize(("pre", "post"), ids(SIGNATURE_PAIRS))
def test_signature_changes(pre: str, post: str) -> None:
    assert named(pre, post) is SIGNATURE


def test_a_default_value_change_is_a_signature_change_deliberately() -> None:
    """``f()`` returned 1 before and returns 2 now: the call contract moved,
    even though no name did. Named as interface rather than body on purpose —
    the documented decision, pinned here so a future 'optimisation' that
    drops defaults from the argspec has to argue with a test."""
    assert named("def f(a=1):\n    return a\n", "def f(a=2):\n    return a\n") is SIGNATURE


def test_signatures_are_a_multiset_not_a_set() -> None:
    """Two identical definitions of one name, one of them deleted. Set
    semantics would call the two sides equal and report a body change; the
    interface genuinely lost a definition."""
    two = "def f(x):\n    return x\n\n\ndef f(x):\n    return x\n"
    one = "def f(x):\n    return x\n"
    assert named(two, one) is SIGNATURE
    assert named(one, two) is SIGNATURE


def test_qualified_names_nest_so_same_named_methods_stay_distinct() -> None:
    """Two classes each own an ``f``. Changing one must not be masked by the
    other's identical bare name."""
    pre = """
    class A:
        def f(self, a):
            return a


    class B:
        def f(self, a):
            return a
    """
    post = """
    class A:
        def f(self, a):
            return a


    class B:
        def f(self, a, b):
            return a
    """
    assert named(pre, post) is SIGNATURE


# --- 2. Import surface changes ------------------------------------------------

IMPORT_PAIRS: list[Pair] = [
    (
        "import-added",
        "import os\n\n\ndef f(a: int) -> None:\n    return None\n",
        "import os\nimport sys\n\n\ndef f(a: int) -> None:\n    return None\n",
    ),
    (
        "import-removed",
        "import os\nimport sys\n\n\ndef f(a: int) -> None:\n    return None\n",
        "import os\n\n\ndef f(a: int) -> None:\n    return None\n",
    ),
    (
        "import-alias-added",
        "import a\n\n\ndef f():\n    return 1\n",
        "import a as b\n\n\ndef f():\n    return 1\n",
    ),
    (
        "import-module-changed",
        "import a.b\n\n\ndef f():\n    return 1\n",
        "import a.c\n\n\ndef f():\n    return 1\n",
    ),
    (
        "from-import-name-added",
        "from x import y\n\n\ndef f():\n    return 1\n",
        "from x import y, z\n\n\ndef f():\n    return 1\n",
    ),
    (
        "from-import-name-removed",
        "from x import y, z\n\n\ndef f():\n    return 1\n",
        "from x import y\n\n\ndef f():\n    return 1\n",
    ),
    (
        "from-import-module-changed",
        "from x import y\n\n\ndef f():\n    return 1\n",
        "from w import y\n\n\ndef f():\n    return 1\n",
    ),
    (
        "from-import-alias-changed",
        "from x import y as z\n\n\ndef f():\n    return 1\n",
        "from x import y as w\n\n\ndef f():\n    return 1\n",
    ),
    (
        "relative-level-changed",
        "from . import x\n\n\ndef f():\n    return 1\n",
        "from .. import x\n\n\ndef f():\n    return 1\n",
    ),
    (
        "relative-becomes-absolute",
        "from .pkg import x\n\n\ndef f():\n    return 1\n",
        "from pkg import x\n\n\ndef f():\n    return 1\n",
    ),
    (
        "star-import-added",
        "from x import y\n\n\ndef f():\n    return 1\n",
        "from x import y\nfrom w import *\n\n\ndef f():\n    return 1\n",
    ),
    (
        "duplicate-import-removed",
        "import a\nimport a\n\n\ndef f():\n    return 1\n",
        "import a\n\n\ndef f():\n    return 1\n",
    ),
    (
        "import-added-inside-a-method",
        """
        class A:
            def f(self):
                return 1
        """,
        """
        class A:
            def f(self):
                import json

                return 1
        """,
    ),
]


@pytest.mark.parametrize(("pre", "post"), ids(IMPORT_PAIRS))
def test_import_changes(pre: str, post: str) -> None:
    assert named(pre, post) is IMPORT


def test_import_moved_between_module_level_and_function_body_is_an_import_change() -> None:
    """The documented decision: imports are keyed by their enclosing
    definition scope, so a deferred import is a change to the import surface
    even though the same name is still imported somewhere. Both directions."""
    top = """
    import json


    def f() -> None:
        json.loads("{}")
    """
    deferred = """
    def f() -> None:
        import json

        json.loads("{}")
    """
    assert named(top, deferred) is IMPORT
    assert named(deferred, top) is IMPORT


def test_imports_are_a_multiset_not_a_set() -> None:
    """A duplicated import statement collapsed to one. Nothing else changed,
    and no signature did — set semantics would report a body change."""
    twice = "import a\nimport a\n\n\ndef f():\n    return 1\n"
    once = "import a\n\n\ndef f():\n    return 1\n"
    assert named(twice, once) is IMPORT


# --- 3. Body changes ----------------------------------------------------------

BODY_PAIRS: list[Pair] = [
    (
        "return-literal-changed",
        "def f():\n    return 1\n",
        "def f():\n    return 2\n",
    ),
    (
        "module-constant-changed",
        "VALUE = 1\n",
        "VALUE = 2\n",
    ),
    (
        "annotated-assignment-changed",
        "VALUE: int = 1\n",
        "VALUE: int = 2\n",
    ),
    (
        "statement-added",
        "def f():\n    a = 1\n    return a\n",
        "def f():\n    a = 1\n    b = 2\n    return a + b\n",
    ),
    (
        "statements-reordered",
        "def f():\n    one()\n    two()\n",
        "def f():\n    two()\n    one()\n",
    ),
    (
        "expression-changed",
        "def f(a, b):\n    return a + b\n",
        "def f(a, b):\n    return a * b\n",
    ),
    (
        "call-argument-changed",
        "def f():\n    return g(1)\n",
        "def f():\n    return g(2)\n",
    ),
    (
        "class-attribute-changed",
        "class A:\n    limit = 1\n",
        "class A:\n    limit = 2\n",
    ),
    (
        "try-except-added",
        "def f():\n    return g()\n",
        "def f():\n    try:\n        return g()\n    except ValueError:\n        return None\n",
    ),
    (
        "lambda-parameter-renamed",
        "f = lambda x: x\n",
        "f = lambda y: y\n",
    ),
    (
        "lambda-parameter-added",
        "f = lambda x: x\n",
        "f = lambda x, y=1: x\n",
    ),
    (
        "lambda-default-changed",
        "f = lambda x=1: x\n",
        "f = lambda x=2: x\n",
    ),
    (
        "type-alias-changed",
        "type Alias = int\n",
        "type Alias = str\n",
    ),
    (
        "global-declaration-added",
        "def f():\n    VALUE = 1\n    return VALUE\n",
        "def f():\n    global VALUE\n    VALUE = 1\n    return VALUE\n",
    ),
    (
        "nonlocal-declaration-added",
        """
        def outer():
            value = 1

            def inner():
                value = 2
                return value

            return inner
        """,
        """
        def outer():
            value = 1

            def inner():
                nonlocal value
                value = 2
                return value

            return inner
        """,
    ),
    (
        "import-statements-reordered",
        "import a\nimport b\n\n\ndef f():\n    return 1\n",
        "import b\nimport a\n\n\ndef f():\n    return 1\n",
    ),
    (
        "definitions-reordered",
        "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n",
        "def beta():\n    return 2\n\n\ndef alpha():\n    return 1\n",
    ),
    (
        "decorator-applied-to-a-lambda-value",
        "f = wrap(lambda x: x)\n",
        "f = wrap(lambda x: x + 1)\n",
    ),
]


@pytest.mark.parametrize(("pre", "post"), ids(BODY_PAIRS))
def test_body_changes(pre: str, post: str) -> None:
    assert named(pre, post) is BODY


def test_a_lambda_signature_change_is_a_body_change() -> None:
    """Lambdas are expressions, not definitions: they have no qualified name
    to be part of an interface. The documented decision."""
    assert named("f = lambda x: x\n", "f = lambda y: y\n") is BODY


def test_wrapping_a_module_level_import_in_type_checking_is_a_body_change() -> None:
    """The documented boundary of the scope-keyed import surface: the module
    still imports ``Path`` in the same scope, so the surface is unchanged and
    only a statement was added. Contrast with the module-level-to-function
    move, which does change the surface of a scope."""
    pre = """
    from typing import TYPE_CHECKING
    from pathlib import Path


    def f(x: "Path") -> None:
        return None
    """
    post = """
    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from pathlib import Path


    def f(x: "Path") -> None:
        return None
    """
    assert named(pre, post) is BODY


def test_reordering_definitions_is_not_a_signature_change() -> None:
    """The documented decision: the signature multiset is unchanged, so the
    ladder falls through — the import surface is unchanged too, and what
    actually differs is the order of the module's statements."""
    pre = "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n"
    post = "def beta():\n    return 2\n\n\ndef alpha():\n    return 1\n"
    assert named(pre, post) is BODY


# --- 4. Priority --------------------------------------------------------------


def test_signature_wins_over_import() -> None:
    pre = "import os\n\n\ndef f(a):\n    return a\n"
    post = "import os\nimport sys\n\n\ndef f(a, b):\n    return a\n"
    assert named(pre, post) is SIGNATURE


def test_signature_wins_over_body() -> None:
    pre = "def f(a):\n    return 1\n"
    post = "def f(a, b):\n    return 2\n"
    assert named(pre, post) is SIGNATURE


def test_import_wins_over_body() -> None:
    pre = "import os\n\n\ndef f(a):\n    return 1\n"
    post = "import os\nimport sys\n\n\ndef f(a):\n    return 2\n"
    assert named(pre, post) is IMPORT


def test_signature_wins_over_import_and_body_together() -> None:
    pre = "import os\n\n\ndef f(a):\n    return 1\n"
    post = "import sys\n\n\ndef f(a, b=2):\n    return 3\n"
    assert named(pre, post) is SIGNATURE


# --- 5. Determinism and totality ---------------------------------------------

ALL_PAIRS: list[Pair] = [
    *[(f"signature:{case}", pre, post) for case, pre, post in SIGNATURE_PAIRS],
    *[(f"import:{case}", pre, post) for case, pre, post in IMPORT_PAIRS],
    *[(f"body:{case}", pre, post) for case, pre, post in BODY_PAIRS],
]

EXPECTED: dict[str, ReasonCode] = {
    **{f"signature:{case}": SIGNATURE for case, _pre, _post in SIGNATURE_PAIRS},
    **{f"import:{case}": IMPORT for case, _pre, _post in IMPORT_PAIRS},
    **{f"body:{case}": BODY for case, _pre, _post in BODY_PAIRS},
}


def test_repeated_calls_on_fresh_parses_agree() -> None:
    """Same trees, same answer — over every pair in the suite, on freshly
    parsed trees each time (so no caching or mutation can hide a difference)."""
    for case, pre, post in ALL_PAIRS:
        first = named(pre, post)
        second = named(pre, post)
        assert first is second is EXPECTED[case], case


def test_every_answer_is_one_of_the_three_escalating_names() -> None:
    """Totality: the function is closed over three reasons, and all three
    escalate — this stage names a change, it never excuses one."""
    allowed = {SIGNATURE, IMPORT, BODY}
    for _case, pre, post in ALL_PAIRS:
        reason = named(pre, post)
        assert reason in allowed
        assert reason not in IRRELEVANT_REASON_CODES


def _classify_in_fresh_interpreter(seed: str) -> str:
    """Every pair's answer, computed by a separate interpreter under an
    explicit PYTHONHASHSEED."""
    pairs = [(case, dedent(pre), dedent(post)) for case, pre, post in ALL_PAIRS]
    script = (
        "import ast\n"
        "from provalume.freshness import _structural\n"
        f"pairs = {pairs!r}\n"
        "for case, pre, post in pairs:\n"
        "    reason = _structural.classify(ast.parse(pre), ast.parse(post))\n"
        "    print(case, reason.value)\n"
    )
    env = {
        **os.environ,
        "PYTHONHASHSEED": seed,
        "PYTHONPATH": str(Path(provalume.__file__).parent.parent),
    }
    completed = subprocess.run(  # noqa: S603 - fixed argv, generated script
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return completed.stdout


def test_answers_do_not_depend_on_the_hash_seed() -> None:
    """Sorted multisets, not set iteration order: two interpreters seeded
    differently must produce byte-identical classifications, and they must
    match what this process computed."""
    first = _classify_in_fresh_interpreter("0")
    second = _classify_in_fresh_interpreter("12345")
    assert first == second
    expected = "".join(f"{case} {EXPECTED[case].value}\n" for case, _pre, _post in ALL_PAIRS)
    assert first == expected


# --- 6. Never raises ----------------------------------------------------------


def test_a_pathological_tree_degrades_to_body() -> None:
    """A hand-built tree missing required fields — the shape a broken code
    generator or a future ast change could hand over. Reading it raises
    inside the classifier; the classifier must not."""
    broken = ast.Module(body=[ast.FunctionDef()], type_ignores=[])
    healthy = ast.parse("def f():\n    return 1\n")
    assert _structural.classify(broken, healthy) is BODY
    assert _structural.classify(healthy, broken) is BODY
    assert _structural.classify(broken, broken) is BODY


def test_a_failing_signature_pass_degrades_to_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """The escalation is already guaranteed by the pipeline; only the name
    degrades. Positive control first, so this cannot pass by never working."""
    pre = ast.parse("def f(a):\n    return 1\n")
    post = ast.parse("def f(a, b):\n    return 1\n")
    assert _structural.classify(pre, post) is SIGNATURE

    def explode(_tree: ast.Module) -> list[tuple[str, ...]]:
        raise RuntimeError("injected: signature collection failed")

    monkeypatch.setattr(_structural, "_signatures", explode)
    assert _structural.classify(pre, post) is BODY


def test_a_failing_import_pass_degrades_to_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """The second rung of the ladder fails open the same way."""
    pre = ast.parse("import os\n\n\ndef f(a):\n    return 1\n")
    post = ast.parse("import sys\n\n\ndef f(a):\n    return 1\n")
    assert _structural.classify(pre, post) is IMPORT

    def explode(_tree: ast.Module) -> list[tuple[str, ...]]:
        raise RuntimeError("injected: import collection failed")

    monkeypatch.setattr(_structural, "_imports", explode)
    assert _structural.classify(pre, post) is BODY


def test_deeply_nested_sources_are_classified_without_raising() -> None:
    """The collectors recurse; a pathologically nested expression must still
    come back with a name rather than a ``RecursionError``."""
    deep_pre = "x = " + "[" * 60 + "]" * 60 + "\n"
    deep_post = "x = " + "[" * 61 + "]" * 61 + "\n"
    assert _structural.classify(ast.parse(deep_pre), ast.parse(deep_post)) is BODY


def test_identical_trees_still_return_a_name() -> None:
    """The pipeline never calls this with equal trees, but a total function
    does not get to depend on its caller being careful."""
    tree = ast.parse("def f(a):\n    return 1\n")
    assert _structural.classify(tree, ast.parse("def f(a):\n    return 1\n")) is BODY
    assert _structural.classify(tree, tree) is BODY
    assert _structural.classify(ast.parse(""), ast.parse("")) is BODY
