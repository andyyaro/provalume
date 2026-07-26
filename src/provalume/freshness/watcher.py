"""The commit watcher: which records did a landed commit undermine?

Pure computation over git plumbing — no execution, no network. Given a
**landed** commit, compute its changed paths, intersect them against the
recorded blast radii, and append one ``freshness.triggered`` event per
touched record; projection flips those records to ``suspect``.

Landed-only is the caller's assertion, consistent with the rule that semantic
truth requires a landing: the CLI and hook documentation say to invoke this
for commits on the integration branch, and the event trail records exactly
which commit was claimed. Worktree state never triggers.

There is no daemon. This runs inside an explicit CLI invocation or an
operator-installed hook, and nowhere else (ADR-0020).

Fail-open (I5): any failure — git unavailable, an unreadable commit — appends
nothing, logs, and never raises. A commit that changed nothing in the
client's world is a clean no-op, not a failure.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

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


def process_landed_commit(pv: Provalume, *, commit_sha: str) -> list[Event]:
    """Trigger freshness for every record ``commit_sha``'s landing touched.

    Returns the appended ``freshness.triggered`` events, in deterministic
    (record-id) order. Empty on a clean no-op and on any failure.
    """
    try:
        git = getattr(pv, "git", None)
        if git is None or not getattr(git, "available", False):
            log.debug("freshness watcher: no repository, nothing to trigger")
            return []
        changed = git.changed_files(commit_sha)
        if changed is None:
            log.warning("freshness watcher: could not read commit %s", commit_sha[:12])
            return []
        if not changed:
            return []
        touched = pv.memories.records_touching(pv.project_id, changed)
        if not touched:
            return []
        recorded_changed = list(changed)
        total = len(recorded_changed)
        if total > MAX_CHANGED_PATHS_RECORDED:
            recorded_changed = []
        events: list[Event] = []
        for record_id, intersecting in touched.items():
            payload = {
                "record_id": record_id,
                "trigger_commit": commit_sha,
                "changed_paths": recorded_changed or list(intersecting),
                "changed_paths_total": total,
                "intersecting_paths": list(intersecting),
            }
            events.append(
                pv.record_event(
                    EventType.FRESHNESS_TRIGGERED,
                    source=Source.KERNEL,
                    payload=payload,
                    commit_sha=commit_sha,
                )
            )
        return events
    except Exception:
        log.warning("freshness watcher failed open", exc_info=True)
        return []
