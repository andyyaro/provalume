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
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

    from provalume.freshness.blast_radius import BlastRadius


def extract(command: str, root: Path) -> BlastRadius | None:
    """Not yet implemented (M1 fan-out unit); fail open until it is."""
    return None
