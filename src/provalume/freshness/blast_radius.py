"""Blast-radius extraction: what code did this verification actually depend on?

Three methods, in descending order of precision (ADR-0020): ``coverage``
observed what actually ran, ``import_graph`` bounds what could run,
``commit_touch`` merely names what changed alongside. The method travels with
the radius because downstream consumers may weight them and may not conflate
them.

Extraction posture:

- On every recorded verification, a radius is extracted via ``import_graph``
  first, ``commit_touch`` as fallback. Recording a verification never
  executes the verification command or any project code — read-only git
  plumbing is the only subprocess this path may reach, and a guard test
  enforces exactly that.
- The ``coverage`` method executes the verification command as a subprocess
  under coverage.py, so it runs only when an operator asks for it explicitly
  (``method=BlastRadiusMethod.COVERAGE``), under a timeout — the same
  operator-present posture as the original verification run.

Everything fails open (I5): any failure logs, returns ``None``, and appends
nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from provalume.schemas.events import EventType
from provalume.schemas.freshness import BlastRadiusMethod
from provalume.schemas.memories import MemoryType
from provalume.schemas.trust import Source

if TYPE_CHECKING:  # pragma: no cover - typing only
    from provalume.schemas.events import Event
    from provalume.sdk.client import Provalume

log = logging.getLogger("provalume.freshness")

#: A radius larger than this is not evidence, it is noise — and it would blow
#: the admission array cap long before it helped anyone. A method that cannot
#: come in under the cap fails (and the next method gets its turn) rather
#: than truncating silently.
MAX_RADIUS_PATHS: Final = 2_000


@dataclass(frozen=True)
class BlastRadius:
    """The contract every extraction method returns.

    ``paths`` are POSIX-style, relative to the repository root, sorted,
    de-duplicated. ``line_ranges`` maps a path to inclusive ``(start, end)``
    ranges and is only ever populated by the ``coverage`` method.
    """

    method: BlastRadiusMethod
    paths: tuple[str, ...]
    line_ranges: dict[str, tuple[tuple[int, int], ...]] | None
    tool: str
    tool_version: str


def record_blast_radius(
    pv: Provalume,
    *,
    record_id: str,
    command: str,
    method: BlastRadiusMethod | None = None,
    commit_sha: str | None = None,
    root: Path | None = None,
    timeout_s: float = 300.0,
) -> Event | None:
    """Extract a blast radius for ``record_id`` and append the event.

    With ``method=None`` (the default), tries the non-executing methods in
    precision order: ``import_graph``, then ``commit_touch``. Pass
    ``method=BlastRadiusMethod.COVERAGE`` to run the command under coverage —
    an explicit act, never implicit. ``root`` overrides the repository root
    (default: the client's git root); without any root, extraction fails
    open — paths that cannot be anchored are not evidence. Returns the
    appended event, or ``None`` on any failure (fail-open, I5).
    """
    try:
        radius = _extract(pv, command=command, method=method, timeout_s=timeout_s, root=root)
        if radius is None:
            return None
        return _emit(pv, record_id=record_id, radius=radius, commit_sha=commit_sha)
    except Exception:
        log.warning("blast-radius extraction failed open", exc_info=True)
        return None


def _emit(
    pv: Provalume, *, record_id: str, radius: BlastRadius, commit_sha: str | None
) -> Event | None:
    payload: dict[str, Any] = {
        "record_id": record_id,
        "method": radius.method.value,
        "paths": list(radius.paths),
        "tool": radius.tool,
        "tool_version": radius.tool_version,
    }
    if radius.line_ranges is not None:
        payload["line_ranges"] = {
            path: [list(r) for r in ranges] for path, ranges in radius.line_ranges.items()
        }
    fields: dict[str, Any] = {}
    if commit_sha:
        fields["commit_sha"] = commit_sha
    return pv.record_event(
        EventType.BLAST_RADIUS_RECORDED,
        source=Source.KERNEL,
        payload=payload,
        **fields,
    )


def record_radii_for_verification(pv: Provalume, event: Event) -> list[Event]:
    """Attach a blast radius to each claim record a verification produced.

    Called by ``record_verification`` after projection. Radii attach to the
    claim types (procedural, gotcha) whose provenance names this event —
    selected by ``source_event_ids``, never by a newest-N page, because a
    repeated failure folds into a gotcha that may be arbitrarily old.
    Extraction runs once; the result is recorded per record. The envelope
    commit is the HEAD extraction actually read, not whatever the caller
    stamped on the verification event. Fail-open: any failure returns what
    was recorded so far.
    """
    recorded: list[Event] = []
    try:
        command = str(event.payload.get("command", ""))
        if not command:
            return recorded
        claim_types = (MemoryType.PROCEDURAL, MemoryType.GOTCHA)
        targets = [
            memory
            for memory in pv.memories.for_source_event(pv.project_id, event.event_id)
            if memory.memory_type in claim_types
        ]
        if not targets:
            return recorded
        radius = _extract(pv, command=command, method=None, timeout_s=300.0)
        if radius is None:
            return recorded
        measured_at = None
        git = getattr(pv, "git", None)
        if git is not None and getattr(git, "available", False):
            measured_at = git.current_commit()
        for memory in targets:
            produced = _emit(pv, record_id=memory.memory_id, radius=radius, commit_sha=measured_at)
            if produced is not None:
                recorded.append(produced)
    except Exception:
        log.warning("radius-on-verification failed open", exc_info=True)
    return recorded


def _extract(
    pv: Provalume,
    *,
    command: str,
    method: BlastRadiusMethod | None,
    timeout_s: float,
    root: Path | None = None,
) -> BlastRadius | None:
    root = root or _repository_root(pv)
    if root is None:
        log.debug("no repository root; blast radius cannot be anchored")
        return None
    if method is BlastRadiusMethod.COVERAGE:
        from provalume.freshness import _coverage

        return _checked(_coverage.extract(command, root, timeout_s=timeout_s))
    if method is BlastRadiusMethod.IMPORT_GRAPH:
        from provalume.freshness import _import_graph

        return _checked(_import_graph.extract(command, root))
    if method is BlastRadiusMethod.COMMIT_TOUCH:
        from provalume.freshness import _commit_touch

        return _checked(_commit_touch.extract(pv, root))
    # Default: non-executing methods in precision order. Recording a
    # verification never runs anything (extraction posture, module docstring).
    from provalume.freshness import _commit_touch, _import_graph

    radius = _checked(_import_graph.extract(command, root))
    if radius is not None:
        return radius
    return _checked(_commit_touch.extract(pv, root))


def _checked(radius: BlastRadius | None) -> BlastRadius | None:
    """A radius over the path cap is noise, not evidence — the method fails."""
    if radius is None:
        return None
    if not radius.paths or len(radius.paths) > MAX_RADIUS_PATHS:
        return None
    return radius


def _repository_root(pv: Provalume) -> Path | None:
    git = getattr(pv, "git", None)
    if git is not None and getattr(git, "available", False):
        return Path(git.root)
    return None
