"""``commit_touch`` extraction: the files changed by the commit that produced
the verification.

The weakest method — it names what changed alongside the evidence, not what
the evidence exercised — and always available wherever there is a repository.
Fallback only; the orchestrator tries ``import_graph`` first.

Contract (frozen at M0/M1 skeleton):

``extract(pv, root) -> BlastRadius | None``

- The relevant commit is the client's current git commit (``pv.git``), i.e.
  the commit the verification was recorded at.
- Changed paths come from git plumbing (``diff-tree --no-commit-id
  --name-only -r``), run through the same argv-only subprocess discipline as
  ``store/gitinfo.py`` — this module opens no subprocess of its own and
  instead calls :meth:`GitInfo.changed_files`, so the timeout and the error
  handling stay in one place. That helper also fixes the three readings of
  "what did this commit change": an ordinary commit is diffed against its
  parent, a merge against its **first** parent (the landing, not the branch
  it landed), and a root commit is every file in it.
- Return ``BlastRadius(method=COMMIT_TOUCH, paths=<sorted POSIX paths
  relative to root>, line_ranges=None, tool="git",
  tool_version=<from `git --version`, e.g. "2.39.2">)``.
- Return ``None`` when there is no usable git context, the commit cannot be
  read, or the diff is empty — and on any error (fail-open, I5). Never
  raise. A radius over ``MAX_RADIUS_PATHS`` fails too, rather than being
  truncated: half a blast radius would understate what a landing touched,
  which is the one direction this axis must never err in.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from provalume.freshness.blast_radius import MAX_RADIUS_PATHS, BlastRadius
from provalume.schemas.freshness import BlastRadiusMethod

if TYPE_CHECKING:  # pragma: no cover - typing only
    from provalume.sdk.client import Provalume

log = logging.getLogger("provalume.freshness")


def extract(pv: Provalume, root: Path) -> BlastRadius | None:
    """The files the current commit changed, as a blast radius.

    Fails open (I5): every failure — and every shape of "there is nothing to
    say here" — is ``None``, logged, never raised.
    """
    try:
        git = pv.git
        if git is None or not git.available:
            log.debug("commit_touch: no repository, so nothing to attribute")
            return None
        # git reports paths relative to the repository it was run in. If that is
        # not `root`, the paths would be anchored to the wrong tree, and a
        # mislabelled path is worse than no radius at all.
        if Path(git.root).resolve() != root.resolve():
            log.debug("commit_touch: %s is not the repository the client reads", root)
            return None

        sha = git.current_commit()
        if not sha:
            log.debug("commit_touch: no current commit to attribute the radius to")
            return None

        paths = git.changed_files(sha)
        if not paths:
            # None (unreadable commit) and () (an empty commit) are both "no
            # evidence here"; only the log line distinguishes them.
            log.debug("commit_touch: %s changed nothing readable", sha[:12])
            return None
        if len(paths) > MAX_RADIUS_PATHS:
            log.debug(
                "commit_touch: %s touched %d paths, over the %d cap",
                sha[:12],
                len(paths),
                MAX_RADIUS_PATHS,
            )
            return None

        version = git.git_version()
        if version is None:
            log.debug("commit_touch: git reported no parseable version")
            return None

        return BlastRadius(
            method=BlastRadiusMethod.COMMIT_TOUCH,
            paths=paths,
            line_ranges=None,
            tool="git",
            tool_version=version,
        )
    except Exception:
        log.warning("commit_touch extraction failed open", exc_info=True)
        return None
