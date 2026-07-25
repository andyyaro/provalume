"""Scope: where a record applies, and how specific that claim is.

Scope is the containment mechanism behind threats T3, T8, and T9. A poisoned or
merely branch-local record stays where it came from unless evidence justifies
widening it, and widening is a promotion with its own rules (ADR-0005).

The hierarchy, narrowest last::

    project -> repository -> branch -> run -> task -> attempt -> agent

``global`` exists as a value and is unreachable: no promotion rule targets it and
attempting to reach it raises. See ADR-0016 for why the value is reserved rather
than omitted.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field


class ScopeLevel(StrEnum):
    """How broadly a record claims to apply."""

    GLOBAL = "global"
    PROJECT = "project"
    REPOSITORY = "repository"
    BRANCH = "branch"
    RUN = "run"
    TASK = "task"
    ATTEMPT = "attempt"
    AGENT = "agent"


#: Breadth ordering. Higher means broader.
SCOPE_BREADTH: Final[dict[ScopeLevel, int]] = {
    ScopeLevel.AGENT: 1,
    ScopeLevel.ATTEMPT: 2,
    ScopeLevel.TASK: 3,
    ScopeLevel.RUN: 4,
    ScopeLevel.BRANCH: 5,
    ScopeLevel.REPOSITORY: 6,
    ScopeLevel.PROJECT: 7,
    ScopeLevel.GLOBAL: 8,
}

#: Not reachable in 0.1.0 (ADR-0016). Enforced by policy, asserted by
#: tests/security/test_scope_isolation.py.
UNREACHABLE_SCOPES: Final[frozenset[ScopeLevel]] = frozenset({ScopeLevel.GLOBAL})

#: Ranking contribution for how well a record's scope matches the query context.
#: Local relevance is real signal: a fact recorded on this branch is more likely
#: to be about what the caller is doing than a project-wide generality.
SCOPE_SPECIFICITY: Final[dict[str, float]] = {
    "branch": 1.0,
    "repository": 0.8,
    "project": 0.6,
    "cross_scope": 0.3,
}


class Applicability(StrEnum):
    """How a record relates to the queried context.

    ``UNCERTAIN`` is used freely and is not a failure. Git ancestry answers
    "could this have been true here?", not "is this true here?", so after a
    rebase or a cherry-pick the honest answer is that Provalume cannot tell
    (ADR-0006). A labelled uncertainty beats a confident wrong answer.
    """

    CURRENT = "current"
    HISTORICAL = "historical"
    CROSS_SCOPE = "cross_scope"
    UNCERTAIN = "uncertain"


class Scope(BaseModel):
    """The scope of a memory record or a query.

    ``project_id`` is mandatory everywhere. It is the one field with no bypass on
    any retrieval path, because cross-project leakage is the only Critical-rated
    confidentiality threat in the model (T9).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    level: ScopeLevel = ScopeLevel.BRANCH
    project_id: str = Field(min_length=1, max_length=256)
    repository_id: str | None = Field(default=None, max_length=256)
    branch: str | None = Field(default=None, max_length=512)
    run_id: str | None = Field(default=None, max_length=128)
    task_id: str | None = Field(default=None, max_length=128)
    attempt_id: str | None = Field(default=None, max_length=128)
    agent_profile: str | None = Field(default=None, max_length=256)

    @property
    def breadth(self) -> int:
        return SCOPE_BREADTH[self.level]

    def widened_to(self, level: ScopeLevel) -> Scope:
        """Return this scope at a broader level, dropping the narrower fields.

        Dropping is deliberate: a record promoted to repository scope must not
        keep claiming a branch, or scope filtering would still confine it. This
        performs no authorisation — :mod:`provalume.policy.scope` decides whether
        the widening is permitted.
        """
        if SCOPE_BREADTH[level] < self.breadth:
            msg = f"cannot widen {self.level} to the narrower {level}"
            raise ValueError(msg)
        keep_branch = SCOPE_BREADTH[level] <= SCOPE_BREADTH[ScopeLevel.BRANCH]
        keep_repo = SCOPE_BREADTH[level] <= SCOPE_BREADTH[ScopeLevel.REPOSITORY]
        return Scope(
            level=level,
            project_id=self.project_id,
            repository_id=self.repository_id if keep_repo else None,
            branch=self.branch if keep_branch else None,
            run_id=None,
            task_id=None,
            attempt_id=None,
            agent_profile=None,
        )

    def describe(self) -> str:
        """Short human-readable form, used in digests and explanations."""
        parts = [f"project={self.project_id}"]
        if self.repository_id:
            parts.append(f"repo={self.repository_id}")
        if self.branch:
            parts.append(f"branch={self.branch}")
        if self.run_id:
            parts.append(f"run={self.run_id}")
        if self.task_id:
            parts.append(f"task={self.task_id}")
        if self.agent_profile:
            parts.append(f"agent={self.agent_profile}")
        return f"{self.level}({', '.join(parts)})"


def specificity(record_scope: Scope, query_scope: Scope) -> tuple[float, Applicability]:
    """Score how well a record's scope matches a query, with a reason.

    Returns the ranking contribution and the applicability label. Both are needed
    because the score drives ordering while the label drives what the digest says
    about the record — a cross-scope hit is still worth showing, just not as
    current truth.

    Callers must have already enforced ``project_id`` equality; this function
    treats a project mismatch as maximally distant rather than silently scoring
    it, so a missing filter shows up as a wrong answer rather than a leak.
    """
    if record_scope.project_id != query_scope.project_id:
        return SCOPE_SPECIFICITY["cross_scope"], Applicability.CROSS_SCOPE

    if record_scope.level is ScopeLevel.PROJECT:
        return SCOPE_SPECIFICITY["project"], Applicability.CURRENT

    same_repo = (
        record_scope.repository_id is None
        or query_scope.repository_id is None
        or record_scope.repository_id == query_scope.repository_id
    )
    if not same_repo:
        return SCOPE_SPECIFICITY["cross_scope"], Applicability.CROSS_SCOPE

    if record_scope.level is ScopeLevel.REPOSITORY:
        return SCOPE_SPECIFICITY["repository"], Applicability.CURRENT

    # Branch-scoped and narrower.
    if record_scope.branch is None or query_scope.branch is None:
        # One side does not name a branch, so branch equality is unknowable.
        return SCOPE_SPECIFICITY["repository"], Applicability.UNCERTAIN
    if record_scope.branch == query_scope.branch:
        return SCOPE_SPECIFICITY["branch"], Applicability.CURRENT
    return SCOPE_SPECIFICITY["cross_scope"], Applicability.CROSS_SCOPE
