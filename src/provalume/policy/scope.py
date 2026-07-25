"""Scope widening policy and path confinement.

Two unrelated jobs live here because both are containment boundaries:

1. **Scope widening** — moving a record from branch to repository to project is a
   promotion with its own evidence requirements (ADR-0005). Global is unreachable
   (ADR-0016).
2. **Path confinement** — resolving a caller-supplied path and refusing anything
   outside its permitted root (threat T21).
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, NamedTuple

from provalume.errors import PathConfinementError
from provalume.schemas.memories import Memory
from provalume.schemas.scope import SCOPE_BREADTH, ScopeLevel
from provalume.schemas.trust import LANDED_STATES, Source

RULE_WIDEN_TO_REPOSITORY: Final = "scope.branch_to_repository.landed"
RULE_WIDEN_TO_PROJECT: Final = "scope.repository_to_project.human_approved"

REFUSE_GLOBAL: Final = "refuse.scope_global_unreachable"
REFUSE_NOT_LANDED: Final = "refuse.scope_widen_not_landed"
REFUSE_NEEDS_HUMAN: Final = "refuse.scope_widen_needs_human"
REFUSE_NARROWING: Final = "refuse.scope_narrowing"
REFUSE_AGENT: Final = "refuse.scope_agent_cannot_widen"


class ScopeDecision(NamedTuple):
    allowed: bool
    rule: str
    reason: str

    def __bool__(self) -> bool:
        return self.allowed


def can_widen(
    memory: Memory,
    target: ScopeLevel,
    *,
    actor_source: Source,
) -> ScopeDecision:
    """Whether a record's scope may be widened to ``target``."""
    current = memory.scope.level

    if target is ScopeLevel.GLOBAL:
        return ScopeDecision(
            False,
            REFUSE_GLOBAL,
            "global scope is not implemented in 0.1.0 and no rule targets it. "
            "Cross-project leakage is the one Critical-rated confidentiality "
            "threat, and the safest answer is that the capability does not exist "
            "(ADR-0016). Use JSONL export and import to move a fact deliberately.",
        )

    if SCOPE_BREADTH[target] <= SCOPE_BREADTH[current]:
        return ScopeDecision(
            False, REFUSE_NARROWING, f"{target} is not broader than {current}"
        )

    if actor_source is Source.AGENT:
        return ScopeDecision(
            False, REFUSE_AGENT, "agents cannot widen scope"
        )

    if target is ScopeLevel.REPOSITORY:
        if memory.integration_state not in LANDED_STATES:
            return ScopeDecision(
                False,
                REFUSE_NOT_LANDED,
                "widening from branch to repository requires landed integration; "
                f"integration_state is {memory.integration_state}",
            )
        return ScopeDecision(
            True, RULE_WIDEN_TO_REPOSITORY, "work landed, so the fact applies repository-wide"
        )

    if target is ScopeLevel.PROJECT:
        if actor_source is not Source.HUMAN:
            return ScopeDecision(
                False,
                REFUSE_NEEDS_HUMAN,
                "widening to project scope requires explicit human approval",
            )
        return ScopeDecision(
            True, RULE_WIDEN_TO_PROJECT, "human approved widening to project scope"
        )

    return ScopeDecision(
        False, REFUSE_NARROWING, f"no rule covers widening {current} -> {target}"
    )


def confine(candidate: Path | str, root: Path | str) -> Path:
    """Resolve ``candidate`` and refuse anything outside ``root`` (threat T21).

    Resolution happens before comparison so ``../``, symlinks, and absolute paths
    are all normalised first — a string-prefix check on an unresolved path is the
    classic bypass.

    ``~`` is expanded before the check. Without expansion, ``~/.ssh/id_rsa``
    joins to ``<root>/~/.ssh/id_rsa`` and passes confinement as an oddly-named
    subdirectory — technically contained, and not what the caller asked for. A
    caller writing ``~`` means the home directory, so expanding and *then*
    refusing is the honest answer.
    """
    root_path = Path(root).expanduser().resolve()
    raw = Path(candidate).expanduser()
    resolved = raw.resolve() if raw.is_absolute() else (root_path / raw).resolve()
    try:
        resolved.relative_to(root_path)
    except ValueError as exc:
        msg = (
            f"path {candidate!r} resolves to {resolved}, which is outside the "
            f"permitted root {root_path}"
        )
        raise PathConfinementError(msg) from exc
    return resolved


def is_confined(candidate: Path | str, root: Path | str) -> bool:
    """Non-raising form of :func:`confine`."""
    try:
        confine(candidate, root)
    except PathConfinementError:
        return False
    return True
