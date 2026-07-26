"""``import_graph`` extraction: the static import closure of the command's
entry points.

Coarser than ``coverage`` — it bounds what *could* be reached, not what ran —
and strictly better than ``commit_touch``. Deterministic, stdlib-``ast``
only, and it must never execute anything.

Contract (frozen at M0/M1 skeleton; the orchestrator and the guard tests
call exactly this):

``extract(command, root) -> BlastRadius | None``

- Parse ``command`` (``shlex``) and find the Python entry points it names:
  path arguments that exist under ``root`` (files or directories, e.g.
  ``tests/`` or ``pkg/mod.py``) and ``-m package`` module references that
  resolve to files under ``root``. Arguments that resolve outside ``root``
  are ignored, not followed.
- Compute the transitive import closure: parse each entry file with
  ``ast``, resolve ``import``/``from`` targets **that live under root**
  (absolute imports against the source roots, and relative imports against
  the importing file), and repeat. Imports that do not resolve to a file
  under ``root`` (stdlib, third-party) are ignored.
- Return ``BlastRadius(method=IMPORT_GRAPH, paths=<sorted POSIX paths
  relative to root>, line_ranges=None, tool="ast",
  tool_version=<the running interpreter's "major.minor">)``.
- Return ``None`` when no entry point resolves, when the closure is empty,
  or on any error (unreadable file, syntax error in an entry file —
  fail-open, I5). Never raise. Never exceed ``MAX_RADIUS_PATHS`` (the
  orchestrator enforces it too, but do not build a million-path list first).

Resolution semantics, where the contract left room (M1, tightened after the
milestone review):

- **Source roots.** Absolute imports and ``-m`` references resolve against
  ``root`` and, when it exists, ``root/src`` — the two layouts that cover
  the overwhelming majority of Python repositories. A hit under either root
  joins the radius (union: over-stating is the safe direction). More exotic
  layouts (configured package dirs) are not probed; LIMITATIONS says so.
- A path argument is an entry only when it is a ``*.py`` file or a
  directory; a directory contributes every ``*.py`` beneath it, recursively,
  skipping dot-directories and vendored trees (``.venv``, ``node_modules``,
  ``build``, ``dist``, caches). A pytest node-id (``tests/x.py::test_y``)
  counts as its file. A non-Python file argument is not an entry.
- For a pytest-shaped command (``pytest …`` or ``-m pytest``), the
  ``conftest.py`` files pytest itself would load join the entries: the one
  at ``root`` and every one on the directory path from ``root`` to each
  entry. pytest executes them; a radius without them is understated.
- A dotted reference pulls the ``__init__.py`` of every package it
  traverses as well as its target; a ``-m`` reference to a package also
  pulls that package's ``__main__.py``, because ``python -m pkg`` executes
  it. A package beats a module of the same name. Directories without
  ``__init__.py`` are traversed as namespace packages (PEP 420).
  Reference components that are not identifiers resolve to nothing.
  When the traversal succeeds but the final component does not resolve to a
  file (a C extension, a generated module, an attribute), the ``__init__``
  chain it walked **stays in the radius** — those files genuinely execute.
- A relative import pulls the ``__init__.py`` of the package its dots name
  in addition to its target. ``from pkg import name`` additionally reaches
  ``pkg/name.py`` when that submodule exists, since the statement alone
  cannot say whether ``name`` is a module or an attribute.
- An entry file that does not parse returns ``None``: the command names it,
  so a radius that silently omitted its imports would understate the blast
  radius. A closure file that does not parse stays *in* the radius — it is
  reachable code — but its own imports go unwalked.
- A closure that would exceed ``MAX_RADIUS_PATHS`` returns ``None`` rather
  than a truncated radius, so the next method gets its turn.
"""

from __future__ import annotations

import ast
import logging
import shlex
import sys
from collections import deque
from pathlib import Path
from typing import Final

from provalume.freshness.blast_radius import MAX_RADIUS_PATHS, BlastRadius
from provalume.schemas.freshness import BlastRadiusMethod

log = logging.getLogger("provalume.freshness")

#: Directory names never walked for entry files: they are not the project's
#: code, and a vendored tree routinely blows the radius cap on its own.
_SKIPPED_DIRS: Final = frozenset(
    {".venv", "venv", "node_modules", "build", "dist", "__pycache__", ".eggs", ".tox"}
)


def extract(command: str, root: Path) -> BlastRadius | None:
    """The import closure of ``command``'s entry points, or ``None``."""
    try:
        anchor = root.resolve()
        roots = _source_roots(anchor)
        entries = _entry_points(command, anchor, roots)
        if not entries or len(entries) > MAX_RADIUS_PATHS:
            return None
        paths = _closure(entries, anchor, roots)
        if not paths:
            return None
        return BlastRadius(
            method=BlastRadiusMethod.IMPORT_GRAPH,
            paths=paths,
            line_ranges=None,
            tool="ast",
            tool_version=f"{sys.version_info.major}.{sys.version_info.minor}",
        )
    except Exception:
        log.debug("import-graph extraction failed open", exc_info=True)
        return None


def _source_roots(root: Path) -> tuple[Path, ...]:
    """Where absolute imports resolve: the root, and ``src/`` when present."""
    src = root / "src"
    return (root, src) if src.is_dir() else (root,)


def _entry_points(command: str, root: Path, roots: tuple[Path, ...]) -> set[Path]:
    """Every file the command's arguments name, anchored under ``root``."""
    args = shlex.split(command)
    entries: set[Path] = set()
    consumed = False
    for index, arg in enumerate(args):
        if consumed:
            consumed = False
            continue
        if arg == "-m":
            consumed = True
            reference = args[index + 1] if index + 1 < len(args) else ""
            if reference:
                for base in roots:
                    entries.update(_module_entry(base, reference))
        elif arg and not arg.startswith("-"):
            entries.update(_path_files(arg, root))
        if len(entries) > MAX_RADIUS_PATHS:
            return entries
    # Conftests only ever augment real entries: a bare `pytest` with no path
    # arguments must fail over to a weaker method rather than produce a
    # conftest-only radius that massively understates the run.
    if entries and _is_pytest_shaped(args):
        entries.update(_conftests(entries, root))
    return entries


def _is_pytest_shaped(args: list[str]) -> bool:
    if not args:
        return False
    if Path(args[0]).name == "pytest":
        return True
    return any(a == "-m" and args[i + 1 : i + 2] == ["pytest"] for i, a in enumerate(args))


def _conftests(entries: set[Path], root: Path) -> set[Path]:
    """The ``conftest.py`` files pytest would load for these entries: the one
    at ``root`` and every one on the path from ``root`` down to each entry.
    pytest executes them, so a radius without them is understated."""
    found: set[Path] = set()
    candidate = root / "conftest.py"
    if candidate.is_file():
        found.add(candidate)
    for entry in entries:
        directory = entry.parent
        while directory.is_relative_to(root):
            candidate = directory / "conftest.py"
            if candidate.is_file():
                found.add(candidate)
            if directory == root:
                break
            directory = directory.parent
    return found


def _path_files(arg: str, root: Path) -> list[Path]:
    """The ``*.py`` files a path argument names — nothing when it does not
    exist, is not Python, or resolves outside ``root``. A pytest node-id
    counts as its file part."""
    bare = arg.split("::", 1)[0]
    candidate = Path(bare)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not resolved.is_relative_to(root):
        return []
    if resolved.is_file():
        return [resolved] if resolved.suffix == ".py" else []
    if not resolved.is_dir():
        return []
    found: list[Path] = []
    stack = [resolved]
    while stack:
        directory = stack.pop()
        for child in sorted(directory.iterdir()):
            name = child.name
            if child.is_dir():
                if name in _SKIPPED_DIRS or name.startswith("."):
                    continue
                stack.append(child)
            elif child.is_file() and child.suffix == ".py":
                found.append(child)
                # Over the cap the radius is discarded whole; stop walking.
                if len(found) > MAX_RADIUS_PATHS:
                    return found
    return found


def _module_entry(base: Path, dotted: str) -> list[Path]:
    """What ``-m dotted`` executes below ``base``: the traversed packages,
    the target — and, for a package target, its ``__main__.py``."""
    files = _module_files(base, dotted)
    parts = dotted.split(".")
    if parts and all(part.isidentifier() for part in parts):
        main = base.joinpath(*parts) / "__main__.py"
        if main.is_file():
            files = [*files, main]
    return files


def _module_files(base: Path, dotted: str) -> list[Path]:
    """The files a dotted reference names below ``base``: the ``__init__.py``
    of every package traversed, then the target itself. The traversed chain
    survives even when the final component does not resolve to a file —
    importing ``a.b.missing`` still executes ``a/__init__.py`` and
    ``a/b/__init__.py``. Empty when the traversal itself fails — stdlib and
    third-party imports land here."""
    if not dotted:
        init = base / "__init__.py"
        return [init] if init.is_file() else []
    parts = dotted.split(".")
    if any(not part.isidentifier() for part in parts):
        return []
    files: list[Path] = []
    current = base
    for part in parts[:-1]:
        current = current / part
        if not current.is_dir():
            return files
        init = current / "__init__.py"
        if init.is_file():
            files.append(init)
    package = current / parts[-1] / "__init__.py"
    module = current / f"{parts[-1]}.py"
    if package.is_file():
        files.append(package)
    elif module.is_file():
        files.append(module)
    return files


def _relative_base(path: Path, root: Path, level: int) -> Path | None:
    """The directory a relative import's dots name, or ``None`` above
    ``root``."""
    base = path.parent
    for _ in range(level - 1):
        base = base.parent
    return base if base.is_relative_to(root) else None


def _imported_files(
    tree: ast.Module, path: Path, root: Path, roots: tuple[Path, ...]
) -> list[Path]:
    """Every file under ``root`` that ``path``'s import statements reach."""
    found: list[Path] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for base in roots:
                    found.extend(_module_files(base, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                bases: tuple[Path, ...] = ()
                relative = _relative_base(path, root, node.level)
                if relative is not None:
                    bases = (relative,)
            else:
                bases = roots
            module = node.module or ""
            for base in bases:
                if node.level:
                    found.extend(_module_files(base, ""))
                found.extend(_module_files(base, module))
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    submodule = f"{module}.{alias.name}" if module else alias.name
                    found.extend(_module_files(base, submodule))
    return [file for file in found if file.is_relative_to(root)]


def _closure(entries: set[Path], root: Path, roots: tuple[Path, ...]) -> tuple[str, ...] | None:
    """The transitive import closure of ``entries``, as sorted POSIX paths
    relative to ``root``. ``None`` over the cap or on an unparseable entry."""
    seen: set[Path] = set()
    queue: deque[Path] = deque(sorted(entries))
    while queue:
        path = queue.popleft()
        if path in seen:
            continue
        seen.add(path)
        if len(seen) > MAX_RADIUS_PATHS:
            return None
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except (SyntaxError, ValueError):
            if path in entries:
                # The command names this file; a radius that silently omitted
                # its imports would understate the blast radius.
                return None
            # Reachable code stays in the radius; its imports go unwalked.
            continue
        for imported in _imported_files(tree, path, root, roots):
            if imported not in seen:
                queue.append(imported)
    return tuple(sorted(p.relative_to(root).as_posix() for p in seen))
