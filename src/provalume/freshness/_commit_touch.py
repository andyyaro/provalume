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
  --name-only -r <sha>``), run through the same argv-only subprocess
  discipline as ``store/gitinfo.py`` — prefer extending ``GitInfo`` with a
  ``changed_files`` helper over open-coding a second subprocess site, so the
  timeout and error handling stay in one place.
- Return ``BlastRadius(method=COMMIT_TOUCH, paths=<sorted POSIX paths
  relative to root>, line_ranges=None, tool="git",
  tool_version=<from `git --version`, e.g. "2.39.2">)``.
- Return ``None`` when there is no usable git context, the commit cannot be
  read, or the diff is empty — and on any error (fail-open, I5). Never
  raise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

    from provalume.freshness.blast_radius import BlastRadius
    from provalume.sdk.client import Provalume


def extract(pv: Provalume, root: Path) -> BlastRadius | None:
    """Not yet implemented (M1 fan-out unit); fail open until it is."""
    return None
