"""The commit watcher: which records did a landed commit undermine?

Pure computation over git plumbing — no execution, no network. Given a
**landed** commit, compute its changed paths, intersect them against the
recorded blast radii, and append one ``freshness.triggered`` event per
touched record; projection flips those records to ``suspect`` and books the
trigger as outstanding until a relevance verdict or a passing re-run
discharges it.

Landed-only is the caller's assertion, consistent with the rule that semantic
truth requires a landing: the CLI and hook documentation say to invoke this
for commits on the integration branch, and the event trail records exactly
which commit was claimed. Worktree state never triggers.

There is no daemon. This runs inside an explicit CLI invocation or an
operator-installed hook, and nowhere else (ADR-0020).

Fail-open (I5) — with honest reporting: an unreadable commit is *degradation*
and says so (``commit_readable=False``); a commit that changed nothing in the
client's world is a clean no-op; a mid-loop failure returns what was actually
appended (``completed=False``) rather than denying work already done. Nothing
here ever raises.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from provalume.schemas.events import EventType
from provalume.schemas.trust import Source

if TYPE_CHECKING:  # pragma: no cover - typing only
    from provalume.schemas.events import Event
    from provalume.sdk.client import Provalume

log = logging.getLogger("provalume.freshness")

#: A monster landing's full path list is context, not evidence; over this
#: bound only the intersecting paths are recorded per trigger (with the true
#: total), so a 10k-file commit cannot fail admission and silently drop every
#: trigger it should have produced.
MAX_CHANGED_PATHS_RECORDED: Final = 2_000


@dataclass
class WatchResult:
    """What one scan did — and whether it could do it at all.

    ``commit_readable=False`` means the commit could not be read (bad sha,
    shallow clone, absent object): the operator's hook is mis-wired and a
    caller must not present it as "nothing to mark". ``completed=False``
    means a mid-scan failure: ``triggered`` still lists every event that was
    actually appended and projected, because denying finished work would be
    its own lie. ``skipped`` counts records whose trigger for this commit
    was already outstanding — re-scanning a commit is idempotent.
    """

    commit_readable: bool = True
    completed: bool = True
    triggered: list[Event] = field(default_factory=list)
    assessed: list[Event] = field(default_factory=list)
    skipped: int = 0


def process_landed_commit(pv: Provalume, *, commit_sha: str) -> WatchResult:
    """Trigger freshness for every record ``commit_sha``'s landing touched.

    Events are appended in deterministic (record-id) order. Never raises.
    """
    result = WatchResult()
    try:
        git = getattr(pv, "git", None)
        if git is None or not getattr(git, "available", False):
            log.debug("freshness watcher: no repository, nothing to trigger")
            result.commit_readable = False
            return result
        changed = git.changed_files(commit_sha)
        if changed is None:
            log.warning("freshness watcher: could not read commit %s", commit_sha[:12])
            result.commit_readable = False
            return result
        if not changed:
            return result
        touched = pv.memories.records_touching(pv.project_id, changed)
        if not touched:
            return result
        recorded_changed = list(changed)
        total = len(recorded_changed)
        if total > MAX_CHANGED_PATHS_RECORDED:
            recorded_changed = []
        for record_id, intersecting in touched.items():
            if pv.memories.trigger_seen(
                project_id=pv.project_id, record_id=record_id, trigger_commit=commit_sha
            ):
                # Already booked: a hook that fires twice must not multiply
                # journal volume (T29).
                result.skipped += 1
                continue
            payload = {
                "record_id": record_id,
                "trigger_commit": commit_sha,
                "changed_paths": recorded_changed or list(intersecting),
                "changed_paths_total": total,
                "intersecting_paths": list(intersecting),
            }
            result.triggered.append(
                pv.record_event(
                    EventType.FRESHNESS_TRIGGERED,
                    source=Source.KERNEL,
                    payload=payload,
                    commit_sha=commit_sha,
                )
            )
            assessment = _assess(pv, git, commit_sha, record_id, intersecting)
            if assessment is not None:
                result.assessed.append(assessment)
        return result
    except Exception:
        log.warning("freshness watcher failed open", exc_info=True)
        result.completed = False
        return result


def _assess(
    pv: Provalume,
    git: Any,
    commit_sha: str,
    record_id: str,
    intersecting: tuple[str, ...],
) -> Event | None:
    """Assess one trigger's relevance and append the verdict event.

    Fail-open in the safe direction: any failure appends nothing, and the
    record simply stays ``suspect`` — escalation is the default, never the
    casualty (spec §5.3).
    """
    try:
        from provalume.freshness import relevance

        parents = git.parents(commit_sha)
        parent = parents[0] if parents else None
        files: list[tuple[str, str | None, str | None]] = []
        for path in intersecting:
            pre = git.file_at(parent, path) if parent else None
            post = git.file_at(commit_sha, path)
            files.append((path, pre, post))
        verdict, reason = relevance.assess_paths(files)
        return pv.record_event(
            EventType.RELEVANCE_ASSESSED,
            source=Source.KERNEL,
            payload={
                "record_id": record_id,
                "trigger_commit": commit_sha,
                "verdict": verdict.value,
                "differ_version": relevance.DIFFER_VERSION,
                "reason_code": reason.value,
            },
            commit_sha=commit_sha,
        )
    except Exception:
        log.warning("relevance assessment failed open; record stays suspect", exc_info=True)
        return None
