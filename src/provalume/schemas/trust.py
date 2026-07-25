"""Trust states and evidence states.

The specification these types implement is ``docs/security/TRUST_MODEL.md``, and
the reasoning for the shape is ADR-0005. If this module and that document
disagree, one of them is a bug — say which.

The central structural point: trust is **two shapes, not one ordering**. A
five-rung ladder that can be compared, and three terminal states that cannot,
because asking "is ``rejected`` more trusted than ``observed``?" is a category
error rather than a hard question.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

__all__ = [
    "CURRENT_TRUTH_STATE",
    "EVIDENCE_SOURCES",
    "LADDER_STATES",
    "LANDED_STATES",
    "MAX_TRUST_RANK",
    "PERMANENT_STATES",
    "SOURCE_CEILING",
    "TERMINAL_STATES",
    "TRUST_RANK",
    "IntegrationState",
    "ReviewState",
    "Source",
    "TrustState",
    "VerificationState",
    "ceiling",
    "evidence_weight",
    "is_permanent",
    "is_terminal",
    "meets",
    "rank",
    "trust_weight",
    "within_ceiling",
]


class TrustState(StrEnum):
    """Where a memory sits in the trust lifecycle. Exactly one per record."""

    QUARANTINED = "quarantined"
    OBSERVED = "observed"
    VERIFIED = "verified"
    REVIEWED = "reviewed"
    INTEGRATED = "integrated"

    INVALIDATED = "invalidated"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


#: The ladder, in order. ``min_trust`` filtering is a rank comparison over this.
TRUST_RANK: Final[dict[TrustState, int]] = {
    TrustState.QUARANTINED: 1,
    TrustState.OBSERVED: 2,
    TrustState.VERIFIED: 3,
    TrustState.REVIEWED: 4,
    TrustState.INTEGRATED: 5,
}

MAX_TRUST_RANK: Final = 5

#: Terminal states. Unranked, excluded from ordinary retrieval, never promoted.
TERMINAL_STATES: Final[frozenset[TrustState]] = frozenset(
    {TrustState.INVALIDATED, TrustState.SUPERSEDED, TrustState.REJECTED}
)

#: Ladder states, for the inverse test.
LADDER_STATES: Final[frozenset[TrustState]] = frozenset(TRUST_RANK)

#: The one state from which there is no return under any circumstances. There is a
#: narrow re-validation path out of INVALIDATED given fresh deterministic
#: evidence, and none out of SUPERSEDED (resolved by writing a new record) or
#: REJECTED. Without this asymmetry there would be a laundering route from
#: rejected work back to trusted truth.
PERMANENT_STATES: Final[frozenset[TrustState]] = frozenset(
    {TrustState.SUPERSEDED, TrustState.REJECTED}
)

#: Only this state may be presented as current project truth without a
#: qualifying label.
CURRENT_TRUTH_STATE: Final = TrustState.INTEGRATED


def rank(state: TrustState) -> int:
    """Ladder rank, or ``0`` for a terminal state.

    Terminal states rank ``0`` so that any ``min_trust`` floor of ``QUARANTINED``
    or above excludes them without a special case at every call site.
    """
    return TRUST_RANK.get(state, 0)


def is_terminal(state: TrustState) -> bool:
    return state in TERMINAL_STATES


def is_permanent(state: TrustState) -> bool:
    """Whether the state can never return to the ladder."""
    return state in PERMANENT_STATES


def meets(state: TrustState, floor: TrustState) -> bool:
    """Whether ``state`` satisfies a ``min_trust`` floor of ``floor``."""
    if is_terminal(state):
        return False
    return rank(state) >= rank(floor)


def trust_weight(state: TrustState) -> float:
    """Normalised ranking contribution in ``[0, 1]`` (ADR-0008)."""
    return rank(state) / MAX_TRUST_RANK


class VerificationState(StrEnum):
    """Whether a deterministic verification ran, and what it returned.

    ``FAILED`` is evidence *present*, not evidence absent. For a gotcha the
    failure is the whole point, so ranking treats ``PASSED`` and ``FAILED``
    alike as "evidence exists" and only ``UNKNOWN`` as its absence. Getting this
    backwards systematically demotes exactly the records the preflight gate needs.
    """

    UNKNOWN = "unknown"
    PASSED = "passed"
    FAILED = "failed"


class ReviewState(StrEnum):
    """The outcome of an independent review, if one happened."""

    NONE = "none"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    REJECTED = "rejected"


class IntegrationState(StrEnum):
    """Whether the work this record describes landed in history."""

    NONE = "none"
    INTEGRATED_RUN = "integrated_run"
    ACCEPTED_USER = "accepted_user"
    REVERTED = "reverted"


#: Integration states that count as landed. ``REVERTED`` deliberately does not:
#: a reverted commit is history, but it is not current truth.
LANDED_STATES: Final[frozenset[IntegrationState]] = frozenset(
    {IntegrationState.INTEGRATED_RUN, IntegrationState.ACCEPTED_USER}
)


class Source(StrEnum):
    """Who produced a record. Structural: assigned by the code path that created
    the event, never read from content.

    A payload claiming ``{"verified": true}`` is payload. This is what makes the
    hardest poisoning case — a clean, confident, false statement — fail: it never
    gets to choose its own source (threat T2).
    """

    HUMAN = "human"
    KERNEL = "kernel"
    ADAPTER = "adapter"
    AGENT = "agent"
    IMPORT = "import"


#: The highest trust state each source can produce *directly*. Going above the
#: ceiling requires additional evidence events, which is what promotion is.
SOURCE_CEILING: Final[dict[Source, TrustState]] = {
    Source.HUMAN: TrustState.INTEGRATED,
    Source.KERNEL: TrustState.VERIFIED,
    Source.ADAPTER: TrustState.VERIFIED,
    Source.AGENT: TrustState.OBSERVED,
    Source.IMPORT: TrustState.OBSERVED,
}

#: Sources trusted to report a deterministic outcome. They are trusted to say
#: *what a command returned*, never to interpret what it means.
EVIDENCE_SOURCES: Final[frozenset[Source]] = frozenset(
    {Source.HUMAN, Source.KERNEL, Source.ADAPTER}
)


def ceiling(source: Source) -> TrustState:
    return SOURCE_CEILING[source]


def within_ceiling(state: TrustState, source: Source) -> bool:
    """Whether ``source`` may produce ``state`` directly.

    Terminal states are always permissible: any source may report that something
    failed, was superseded, or was rejected. Withdrawal is not a trust grant, so
    it needs no authority — and refusing an agent's ability to report a failure
    would suppress exactly the information gotcha memory exists to capture.
    """
    if is_terminal(state):
        return True
    return rank(state) <= rank(ceiling(source))


def evidence_weight(
    verification: VerificationState,
    review: ReviewState,
    integration: IntegrationState,
) -> float:
    """Normalised evidence contribution in ``[0, 1]`` (ADR-0008).

    Weights: verification ran 0.4, review approved 0.3, work landed 0.3.
    """
    score = 0.0
    if verification is not VerificationState.UNKNOWN:
        score += 0.4
    if review is ReviewState.APPROVED:
        score += 0.3
    if integration in LANDED_STATES:
        score += 0.3
    return min(1.0, score)
