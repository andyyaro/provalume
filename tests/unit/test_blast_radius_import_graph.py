"""``import_graph`` blast-radius extraction: entry points, closure, fail-open.

Every case builds a throwaway project tree under ``tmp_path`` — the extractor
never reads this repository, and never executes anything.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from provalume.freshness import _import_graph
from provalume.freshness.blast_radius import MAX_RADIUS_PATHS, BlastRadius
from provalume.schemas.freshness import BlastRadiusMethod


def project(tmp_path: Path, files: dict[str, str]) -> Path:
    """A project root containing exactly ``files`` (path -> source)."""
    root = tmp_path / "proj"
    root.mkdir(parents=True, exist_ok=True)
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    return root


def paths(radius: BlastRadius | None) -> set[str]:
    assert radius is not None
    return set(radius.paths)


# --- Entry points ------------------------------------------------------------


def test_single_file_entry_and_transitive_closure(tmp_path: Path) -> None:
    """a imports b imports c: the closure follows every hop."""
    root = project(
        tmp_path,
        {
            "a.py": "import b\n",
            "b.py": "import c\n",
            "c.py": "VALUE = 1\n",
            "unrelated.py": "import c\n",
        },
    )
    radius = _import_graph.extract("python a.py", root)
    assert paths(radius) == {"a.py", "b.py", "c.py"}


def test_directory_entry_expands_recursively(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "mod.py": "VALUE = 1\n",
            "tests/test_mod.py": "import mod\n",
            "tests/nested/test_deep.py": "VALUE = 2\n",
            "tests/data.txt": "not python\n",
        },
    )
    radius = _import_graph.extract(f"{sys.executable} -m pytest tests/", root)
    assert paths(radius) == {"mod.py", "tests/test_mod.py", "tests/nested/test_deep.py"}


def test_bare_pytest_command_resolves_the_directory_argument(tmp_path: Path) -> None:
    """No explicit interpreter: the path argument is still an entry point."""
    root = project(tmp_path, {"mod.py": "VALUE = 1\n", "tests/test_mod.py": "import mod\n"})
    radius = _import_graph.extract("pytest -q tests/", root)
    assert paths(radius) == {"mod.py", "tests/test_mod.py"}


def test_module_entry_pulls_the_package_init(tmp_path: Path) -> None:
    """``-m pkg.mod`` executes ``pkg/__init__.py`` before ``pkg/mod.py``."""
    root = project(
        tmp_path,
        {
            "pkg/__init__.py": "import shared\n",
            "pkg/mod.py": "VALUE = 1\n",
            "shared.py": "VALUE = 2\n",
        },
    )
    radius = _import_graph.extract("python -m pkg.mod", root)
    assert paths(radius) == {"pkg/__init__.py", "pkg/mod.py", "shared.py"}


def test_module_entry_that_names_a_package_resolves_its_init(tmp_path: Path) -> None:
    root = project(tmp_path, {"pkg/__init__.py": "VALUE = 1\n", "pkg/mod.py": "VALUE = 2\n"})
    radius = _import_graph.extract("python -m pkg", root)
    assert paths(radius) == {"pkg/__init__.py"}


def test_unresolvable_module_entry_is_ignored(tmp_path: Path) -> None:
    """``-m pytest`` names a third-party module, not project code."""
    root = project(tmp_path, {"mod.py": "VALUE = 1\n"})
    assert _import_graph.extract("python -m pytest", root) is None


def test_non_python_file_arguments_are_not_entries(tmp_path: Path) -> None:
    root = project(tmp_path, {"run.sh": "#!/bin/sh\nexit 0\n", "setup.cfg": "[metadata]\n"})
    assert _import_graph.extract("bash run.sh setup.cfg", root) is None


# --- Import resolution -------------------------------------------------------


def test_from_module_import_name_reaches_the_module(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "entry.py": "from pkg.mod import thing\n",
            "pkg/__init__.py": "",
            "pkg/mod.py": "thing = 1\n",
        },
    )
    radius = _import_graph.extract("python entry.py", root)
    assert paths(radius) == {"entry.py", "pkg/__init__.py", "pkg/mod.py"}


def test_from_package_import_submodule_reaches_the_submodule(tmp_path: Path) -> None:
    """``name`` may be a module or an attribute; the statement cannot say."""
    root = project(
        tmp_path,
        {
            "entry.py": "from pkg import mod, MISSING\n",
            "pkg/__init__.py": "",
            "pkg/mod.py": "VALUE = 1\n",
        },
    )
    radius = _import_graph.extract("python entry.py", root)
    assert paths(radius) == {"entry.py", "pkg/__init__.py", "pkg/mod.py"}


def test_relative_imports_resolve_against_the_importing_package(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/util.py": "CONST = 1\n",
            "pkg/sub/__init__.py": "",
            "pkg/sub/helper.py": "VALUE = 1\n",
            "pkg/sub/leaf.py": "from . import helper\nfrom ..util import CONST\n",
        },
    )
    radius = _import_graph.extract("python pkg/sub/leaf.py", root)
    assert paths(radius) == {
        "pkg/__init__.py",
        "pkg/util.py",
        "pkg/sub/__init__.py",
        "pkg/sub/helper.py",
        "pkg/sub/leaf.py",
    }


def test_relative_import_above_root_is_ignored(tmp_path: Path) -> None:
    root = project(tmp_path, {"entry.py": "from ... import escape\n", "keep.py": "VALUE = 1\n"})
    radius = _import_graph.extract("python entry.py", root)
    assert paths(radius) == {"entry.py"}


def test_namespace_packages_are_traversed(tmp_path: Path) -> None:
    """PEP 420: a directory without ``__init__.py`` is still a package."""
    root = project(tmp_path, {"entry.py": "import ns.mod\n", "ns/mod.py": "VALUE = 1\n"})
    radius = _import_graph.extract("python entry.py", root)
    assert paths(radius) == {"entry.py", "ns/mod.py"}


def test_stdlib_and_third_party_imports_are_ignored(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "entry.py": "import os\nimport sys\nimport pytest\nfrom pathlib import Path\n",
        },
    )
    radius = _import_graph.extract("python entry.py", root)
    assert paths(radius) == {"entry.py"}


def test_star_import_reaches_the_module(tmp_path: Path) -> None:
    root = project(tmp_path, {"entry.py": "from pkg.mod import *\n", "pkg/mod.py": "VALUE = 1\n"})
    radius = _import_graph.extract("python entry.py", root)
    assert paths(radius) == {"entry.py", "pkg/mod.py"}


def test_conditional_and_nested_imports_are_reached(tmp_path: Path) -> None:
    """The walk is over the whole tree, not just module-level statements."""
    root = project(
        tmp_path,
        {
            "entry.py": "def load():\n    import late\n\n\nif False:\n    import dead\n",
            "late.py": "VALUE = 1\n",
            "dead.py": "VALUE = 2\n",
        },
    )
    radius = _import_graph.extract("python entry.py", root)
    assert paths(radius) == {"entry.py", "late.py", "dead.py"}


def test_import_cycles_terminate(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "a.py": "import b\n",
            "b.py": "import c\n",
            "c.py": "import a\n",
        },
    )
    radius = _import_graph.extract("python a.py", root)
    assert paths(radius) == {"a.py", "b.py", "c.py"}


# --- Shape and determinism ---------------------------------------------------


def test_radius_carries_the_frozen_contract_shape(tmp_path: Path) -> None:
    root = project(tmp_path, {"mod.py": "VALUE = 1\n"})
    radius = _import_graph.extract("python mod.py", root)
    assert radius is not None
    assert radius.method is BlastRadiusMethod.IMPORT_GRAPH
    assert radius.line_ranges is None
    assert radius.tool == "ast"
    assert radius.tool_version == f"{sys.version_info.major}.{sys.version_info.minor}"
    assert radius.paths == ("mod.py",)


def test_paths_are_sorted_deduplicated_posix_and_deterministic(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "z.py": "import pkg.a\n",
            "pkg/__init__.py": "",
            "pkg/a.py": "import z\n",
            "tests/test_z.py": "import z\n",
        },
    )
    command = "pytest tests/ tests/test_z.py z.py z.py"
    radius = _import_graph.extract(command, root)
    assert radius is not None
    assert radius.paths == ("pkg/__init__.py", "pkg/a.py", "tests/test_z.py", "z.py")
    assert len(set(radius.paths)) == len(radius.paths)
    assert all("\\" not in path and not path.startswith("/") for path in radius.paths)
    assert _import_graph.extract(command, root) == radius


def test_root_is_resolved_before_paths_are_relativised(tmp_path: Path) -> None:
    """An unresolved root (symlink, ``..``) must not leak absolute paths."""
    root = project(tmp_path, {"mod.py": "VALUE = 1\n"})
    radius = _import_graph.extract("python mod.py", root / "sub" / "..")
    assert paths(radius) == {"mod.py"}


# --- Arguments that are not entry points -------------------------------------


def test_arguments_outside_root_are_ignored(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "outside.py").write_text("SECRET = 1\n")
    root = project(tmp_path, {"mod.py": "VALUE = 1\n"})
    radius = _import_graph.extract("python mod.py /etc/passwd ../elsewhere/outside.py /etc", root)
    assert paths(radius) == {"mod.py"}


def test_only_outside_arguments_resolve_to_nothing(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "outside.py").write_text("SECRET = 1\n")
    root = project(tmp_path, {"mod.py": "VALUE = 1\n"})
    assert _import_graph.extract("python ../elsewhere/outside.py", root) is None


def test_module_reference_cannot_escape_root(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "outside.py").write_text("SECRET = 1\n")
    root = project(tmp_path, {"mod.py": "VALUE = 1\n"})
    assert _import_graph.extract("python -m ..elsewhere.outside", root) is None
    assert _import_graph.extract("python -m /etc/passwd", root) is None


def test_nonexistent_path_arguments_are_ignored(tmp_path: Path) -> None:
    root = project(tmp_path, {"mod.py": "VALUE = 1\n"})
    radius = _import_graph.extract("python mod.py gone.py missing/", root)
    assert paths(radius) == {"mod.py"}


def test_no_resolvable_entry_returns_none(tmp_path: Path) -> None:
    root = project(tmp_path, {"mod.py": "VALUE = 1\n"})
    assert _import_graph.extract("make build", root) is None
    assert _import_graph.extract("pytest -q -k smoke", root) is None
    assert _import_graph.extract("", root) is None


def test_empty_directory_argument_returns_none(tmp_path: Path) -> None:
    root = project(tmp_path, {"tests/data.txt": "not python\n"})
    assert _import_graph.extract("pytest tests/", root) is None


def test_unparseable_command_returns_none(tmp_path: Path) -> None:
    root = project(tmp_path, {"mod.py": "VALUE = 1\n"})
    assert _import_graph.extract("pytest 'unbalanced mod.py", root) is None


# --- Fail-open (I5) ----------------------------------------------------------


def test_syntax_error_in_an_entry_file_returns_none(tmp_path: Path) -> None:
    root = project(tmp_path, {"broken.py": "def f(:\n", "mod.py": "VALUE = 1\n"})
    assert _import_graph.extract("python broken.py", root) is None
    assert _import_graph.extract("pytest .", root) is None


def test_syntax_error_in_a_closure_file_keeps_it_but_stops_the_walk(tmp_path: Path) -> None:
    """Reachable code stays in the radius; its unknowable imports do not."""
    root = project(
        tmp_path,
        {
            "entry.py": "import broken\n",
            "broken.py": "import hidden\ndef f(:\n",
            "hidden.py": "VALUE = 1\n",
        },
    )
    radius = _import_graph.extract("python entry.py", root)
    assert paths(radius) == {"entry.py", "broken.py"}


def test_unreadable_file_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = project(tmp_path, {"mod.py": "VALUE = 1\n"})

    def explode(*args: object, **kwargs: object) -> str:
        raise OSError("injected: unreadable file")

    monkeypatch.setattr(Path, "read_text", explode)
    assert _import_graph.extract("python mod.py", root) is None


def test_missing_root_returns_none(tmp_path: Path) -> None:
    assert _import_graph.extract("pytest tests/", tmp_path / "gone") is None


def test_a_closure_over_the_cap_is_discarded_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Over the cap the method fails so the next one gets its turn — a
    truncated radius would be a lie about what the command reaches."""
    root = project(
        tmp_path,
        {
            "a.py": "import b\n",
            "b.py": "import c\n",
            "c.py": "VALUE = 1\n",
        },
    )
    monkeypatch.setattr(_import_graph, "MAX_RADIUS_PATHS", 2)
    assert _import_graph.extract("python a.py", root) is None
    monkeypatch.setattr(_import_graph, "MAX_RADIUS_PATHS", MAX_RADIUS_PATHS)
    assert _import_graph.extract("python a.py", root) is not None


def test_extraction_never_executes_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I5/I1: static analysis only — no subprocess, ever."""
    import subprocess

    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("import-graph extraction executed a subprocess")

    monkeypatch.setattr(subprocess, "run", explode)
    monkeypatch.setattr(subprocess, "Popen", explode)
    root = project(tmp_path, {"mod.py": "import os\n", "tests/test_mod.py": "import mod\n"})
    radius = _import_graph.extract(f"{sys.executable} -m pytest tests/", root)
    assert paths(radius) == {"mod.py", "tests/test_mod.py"}
