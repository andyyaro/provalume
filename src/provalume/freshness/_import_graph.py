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
  (absolute imports against top-level packages present in ``root``, and
  relative imports against the importing file), and repeat. Imports that do
  not resolve to a file under ``root`` (stdlib, third-party) are ignored.
- Return ``BlastRadius(method=IMPORT_GRAPH, paths=<sorted POSIX paths
  relative to root>, line_ranges=None, tool="ast",
  tool_version=<the running interpreter's "major.minor">)``.
- Return ``None`` when no entry point resolves, when the closure is empty,
  or on any error (unreadable file, syntax error in an entry file —
  fail-open, I5). Never raise. Never exceed ``MAX_RADIUS_PATHS`` (the
  orchestrator enforces it too, but do not build a million-path list first).

Resolution semantics, where the contract left room (M1):

- A path argument is an entry only when it is a ``*.py`` file or a
  directory; a directory contributes every ``*.py`` beneath it, recursively.
  A non-Python file argument (``pytest.ini``, a shell script) is not an
  entry — parsing it would fail the whole extraction over an argument that
  was never Python.
- A dotted reference pulls the ``__init__.py`` of every package it traverses
  as well as its target, because importing ``pkg.mod`` executes
  ``pkg/__init__.py``. A package beats a module of the same name, matching
  the import system. Directories without ``__init__.py`` are traversed as
  namespace packages (PEP 420). Reference components that are not
  identifiers resolve to nothing — ``-m ..escape`` is not a path.
- A relative import pulls the ``__init__.py`` of the package its dots name
  in addition to its target: ``from ..util import CONST`` under ``pkg/sub/``
  reaches ``pkg/__init__.py`` and ``pkg/util.py``. ``from pkg import name``
  additionally reaches ``pkg/name.py`` when that submodule exists, since the
  statement alone cannot say whether ``name`` is a module or an attribute.
- An entry file that does not parse returns ``None``: the command names it,
  so a radius that silently omitted its imports would understate the blast
  radius. A closure file that does not parse stays *in* the radius — it is
  reachable code — but its own imports go unwalked; dropping reachable code
  would understate the radius in the other direction.
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

from provalume.freshness.blast_radius import MAX_RADIUS_PATHS, BlastRadius
from provalume.schemas.freshness import BlastRadiusMethod

log = logging.getLogger("provalume.freshness")


def extract(command: str, root: Path) -> BlastRadius | None:
    """The import closure of ``command``'s entry points, or ``None``."""
    try:
        anchor = root.resolve()
        entries = _entry_points(command, anchor)
        if not entries or len(entries) > MAX_RADIUS_PATHS:
            return None
        paths = _closure(entries, anchor)
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


def _entry_points(command: str, root: Path) -> set[Path]:
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
                entries.update(_module_files(root, reference))
        elif arg and not arg.startswith("-"):
            entries.update(_path_files(arg, root))
        if len(entries) > MAX_RADIUS_PATHS:
            return entries
    return entries


def _path_files(arg: str, root: Path) -> list[Path]:
    """The ``*.py`` files a path argument names — nothing when it does not
    exist, is not Python, or resolves outside ``root``."""
    candidate = Path(arg)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not resolved.is_relative_to(root):
        return []
    if resolved.is_file():
        return [resolved] if resolved.suffix == ".py" else []
    if not resolved.is_dir():
        return []
    found: list[Path] = []
    for path in resolved.rglob("*.py"):
        if path.is_file():
            found.append(path)
            # Over the cap the radius is discarded whole; stop walking.
            if len(found) > MAX_RADIUS_PATHS:
                break
    return found


def _module_files(base: Path, dotted: str) -> list[Path]:
    """The files a dotted reference names below ``base``: the ``__init__.py``
    of every package traversed, then the target itself. Empty when the
    reference does not resolve below ``base`` — stdlib and third-party
    imports land here."""
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
            return []
        init = current / "__init__.py"
        if init.is_file():
            files.append(init)
    package = current / parts[-1] / "__init__.py"
    module = current / f"{parts[-1]}.py"
    if package.is_file():
        files.append(package)
    elif module.is_file():
        files.append(module)
    else:
        return []
    return files


def _relative_base(path: Path, root: Path, level: int) -> Path | None:
    """The directory a relative import's dots name, or ``None`` above
    ``root``."""
    base = path.parent
    for _ in range(level - 1):
        base = base.parent
    return base if base.is_relative_to(root) else None


def _imported_files(tree: ast.Module, path: Path, root: Path) -> list[Path]:
    """Every file under ``root`` that ``path``'s import statements reach."""
    found: list[Path] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.extend(_module_files(root, alias.name))
        elif isinstance(node, ast.ImportFrom):
            base = _relative_base(path, root, node.level) if node.level else root
            if base is None:
                continue
            module = node.module or ""
            if node.level:
                found.extend(_module_files(base, ""))
            found.extend(_module_files(base, module))
            for alias in node.names:
                if alias.name == "*":
                    continue
                submodule = f"{module}.{alias.name}" if module else alias.name
                found.extend(_module_files(base, submodule))
    return [file for file in found if file.is_relative_to(root)]


def _closure(entries: set[Path], root: Path) -> tuple[str, ...] | None:
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
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source, filename=str(path))
        except (SyntaxError, ValueError):
            if path in entries:
                return None
            # Reachable code whose own imports are unknowable: keep the
            # file, walk nothing.
            continue
        queue.extend(_imported_files(tree, path, root))
    return tuple(sorted(path.relative_to(root).as_posix() for path in seen))
