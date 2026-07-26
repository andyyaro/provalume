"""The freshness engine: code-grounded invalidation (ADR-0020).

Everything in this package is deterministic stdlib + provalume — no model
call, no network, no third-party import (invariant I1, guarded by
``tests/security/test_freshness_invariants.py``). Every entry point fails
open (I5): an internal failure returns ``None`` or does nothing, logs, and
never disturbs the record it was asked about. Nothing here can move a trust
state (I4); the package's events move the freshness axis only.

This package is the one place outside ``store/`` allowed to use
``subprocess`` — for coverage extraction and, behind the T27 control set,
gated re-execution. Argument vectors only; never ``shell=True``.
"""

from __future__ import annotations

import logging

from provalume.freshness.blast_radius import BlastRadius, record_blast_radius
from provalume.freshness.watcher import WatchResult, process_landed_commit

#: The spec requires fail-open paths to log. The library is otherwise silent,
#: so the logger ships with a NullHandler — operators opt in by configuring
#: the ``provalume.freshness`` logger; nothing is emitted by default.
logging.getLogger("provalume.freshness").addHandler(logging.NullHandler())

__all__ = [
    "BlastRadius",
    "WatchResult",
    "process_landed_commit",
    "record_blast_radius",
]
