"""Provenance: the evidence chain behind a memory.

The product claim is "facts your agents proved". This module holds the types that
answer *proved by what?* — and, critically, the types that admit when the answer
cannot be resolved.

A memory whose claimed provenance does not resolve is **degraded, visibly**. It is
not dropped (the record of the attempt is still useful) and it is not served as
though the chain held (that would be the forged-provenance threat, T15). The
degradation appears in ``explain`` output and in ``audit``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from provalume.schemas.scope import Applicability
from provalume.schemas.trust import (
    IntegrationState,
    ReviewState,
    Source,
    TrustState,
    VerificationState,
)


class ResolutionStatus(StrEnum):
    """Whether a provenance claim could be checked against reality."""

    RESOLVED = "resolved"
    """Checked and holds: the events exist, the commit exists."""

    UNRESOLVABLE = "unresolvable"
    """Could not be checked — no repository available, commit garbage-collected,
    branch deleted, history rewritten. Not evidence of forgery; evidence of not
    knowing. Applicability degrades to uncertain."""

    BROKEN = "broken"
    """Checked and does not hold: a referenced event is absent from the journal, or
    a hash does not match. This is a hard finding — ``audit`` fails on it."""


class VerificationEvidence(BaseModel):
    """A verification that ran, and what it returned."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    command: str = Field(max_length=4_096)
    passed: bool
    exit_code: int | None = None
    failure_signature: str | None = None
    recorded_at: str
    source: Source
    excerpt: str = Field(default="", max_length=8_192)
    """Bounded, already-redacted output excerpt. Bounded because the full output
    of a failing build can be megabytes (threat T25)."""


class ReviewEvidence(BaseModel):
    """An independent review verdict.

    ``independent`` is computed, not asserted: it is ``reviewer != author``. A
    self-review never promotes, and recording the comparison result means the
    refusal is explainable rather than mysterious.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    reviewer: str = Field(max_length=256)
    verdict: ReviewState
    independent: bool
    findings: tuple[str, ...] = ()
    recorded_at: str
    source: Source


class IntegrationEvidence(BaseModel):
    """A commit that landed, or was reverted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    commit_sha: str = Field(max_length=64)
    branch: str | None = Field(default=None, max_length=512)
    state: IntegrationState
    recorded_at: str
    source: Source
    resolution: ResolutionStatus = ResolutionStatus.UNRESOLVABLE
    """Whether the commit could be found in a repository. Defaults to
    unresolvable so an unchecked claim never looks checked."""


class DecisionEvidence(BaseModel):
    """A human decision, with its rejected alternatives.

    Rejected alternatives are the reusable part: a decision record that says only
    what was chosen cannot stop an agent from re-proposing what was rejected.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    selected: str = Field(max_length=2_048)
    rejected: tuple[str, ...] = ()
    rationale: str = Field(default="", max_length=8_192)
    authority: str = Field(default="", max_length=256)
    consequences: str = Field(default="", max_length=4_096)
    recorded_at: str
    source: Source


class Provenance(BaseModel):
    """The full evidence chain for one memory.

    Assembled on demand rather than stored, so it always reflects the current
    journal. Storing it would create a second source of truth that could drift
    from the events it summarises.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_id: str
    trust_state: TrustState
    verification_state: VerificationState
    review_state: ReviewState
    integration_state: IntegrationState

    source_event_ids: tuple[str, ...] = ()
    verifications: tuple[VerificationEvidence, ...] = ()
    reviews: tuple[ReviewEvidence, ...] = ()
    integrations: tuple[IntegrationEvidence, ...] = ()
    decisions: tuple[DecisionEvidence, ...] = ()

    author_agent: str | None = None
    adapter: str | None = None
    model: str | None = None
    effort: str | None = None

    supersedes_id: str | None = None
    superseded_by_id: str | None = None
    resolves_gotcha_id: str | None = None
    """Set when this record documents what finally worked after a failure. The
    link is what turns a gotcha from "this broke" into "this broke, and here is
    the fix"."""

    resolution: ResolutionStatus = ResolutionStatus.UNRESOLVABLE
    resolution_detail: str = ""
    applicability: Applicability = Applicability.UNCERTAIN

    transitions: tuple[dict[str, Any], ...] = ()
    """Lifecycle history, newest first. Each entry names the policy rule that
    authorised or refused the transition."""

    @property
    def has_independent_review(self) -> bool:
        return any(r.independent and r.verdict is ReviewState.APPROVED for r in self.reviews)

    @property
    def passing_verifications(self) -> tuple[VerificationEvidence, ...]:
        return tuple(v for v in self.verifications if v.passed)

    @property
    def failing_verifications(self) -> tuple[VerificationEvidence, ...]:
        return tuple(v for v in self.verifications if not v.passed)

    def describe(self) -> str:
        """One-line human summary, used in digests.

        Reads as evidence rather than as a status code, because this string is
        what a reader actually sees next to a memory.
        """
        parts: list[str] = [f"trust={self.trust_state}"]
        passing = self.passing_verifications
        failing = self.failing_verifications
        if passing:
            parts.append(f"verified by `{passing[0].command}`")
        elif failing:
            parts.append(f"failed `{failing[0].command}`")
        approvals = [r for r in self.reviews if r.verdict is ReviewState.APPROVED]
        if approvals:
            reviewer = approvals[0].reviewer
            independent = "" if approvals[0].independent else " (not independent)"
            parts.append(f"approved by {reviewer}{independent}")
        landed = [i for i in self.integrations if i.state in {
            IntegrationState.INTEGRATED_RUN,
            IntegrationState.ACCEPTED_USER,
        }]
        if landed:
            parts.append(f"landed in {landed[0].commit_sha[:12]}")
        if self.resolution is not ResolutionStatus.RESOLVED:
            parts.append(f"provenance {self.resolution}")
        return "; ".join(parts)
