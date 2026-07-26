"""Boundary 2: the only place trust is granted.

This module implements ``docs/security/TRUST_MODEL.md``. Every decision returns a
:class:`Decision` naming the **policy rule** that authorised or refused it, and
the specific evidence events relied on. That naming is what makes the trust model
falsifiable rather than decorative: for any record you can ask *which rule
promoted this, on what evidence* and get an answer, not a shrug.

Refusals are returned as decisions too, and the caller records them. A promotion
attempt that silently vanishes is exactly what an attacker wants.
"""

from __future__ import annotations

from typing import Final, NamedTuple

from provalume.schemas.events import Event, EventType
from provalume.schemas.memories import (
    NEVER_INTEGRATED,
    TYPE_CEILING_WITHOUT_INTEGRATION,
    Memory,
    MemoryType,
)
from provalume.schemas.trust import (
    LANDED_STATES,
    IntegrationState,
    ReviewState,
    Source,
    TrustState,
    is_permanent,
    is_terminal,
    rank,
)


class Decision(NamedTuple):
    """The outcome of evaluating one transition.

    ``rule`` is always populated — on refusal it names the rule that refused, so
    "why was this not promoted?" is as answerable as "why was this promoted?".
    """

    allowed: bool
    rule: str
    reason: str
    evidence_event_ids: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.allowed


# --- Rule names -----------------------------------------------------------
# Stored in memory_transitions.policy_rule and surfaced by `provalume explain`.
# Treat them as a stable vocabulary: renaming one makes historical transitions
# unreadable.

RULE_QUARANTINE_TO_OBSERVED: Final = "promote.any.evidence_linked"
RULE_OBSERVED_TO_VERIFIED: Final = "promote.any.verified_by_evidence"
RULE_PROCEDURAL_VERIFIED: Final = "promote.procedural.verified_by_exact_command"
RULE_GOTCHA_VERIFIED: Final = "promote.gotcha.verified_by_failure_evidence"
RULE_VERIFIED_TO_REVIEWED: Final = "promote.any.independent_review_approved"
RULE_REVIEWED_TO_INTEGRATED: Final = "promote.any.landed_in_history"
RULE_DECISION_HUMAN: Final = "promote.decision.human_authority"
RULE_SEMANTIC_VERIFIED: Final = "promote.semantic.landed_or_human_authority"
RULE_REVALIDATE: Final = "revalidate.invalidated.fresh_evidence"

REFUSE_TERMINAL: Final = "refuse.terminal_state"
REFUSE_PERMANENT: Final = "refuse.permanent_state"
REFUSE_REJECTED: Final = "refuse.rejected_never_promoted"
REFUSE_SKIPPED_RUNG: Final = "refuse.rung_skipped"
REFUSE_NO_EVIDENCE: Final = "refuse.no_qualifying_evidence"
REFUSE_SELF_REVIEW: Final = "refuse.self_review"
REFUSE_SOURCE_CEILING: Final = "refuse.source_ceiling"
REFUSE_TYPE_CEILING: Final = "refuse.type_ceiling"
REFUSE_POISONING: Final = "refuse.poisoning_risk"
REFUSE_AGENT_ACTOR: Final = "refuse.agent_cannot_promote"
REFUSE_NOT_LANDED: Final = "refuse.not_landed"
REFUSE_NEVER_INTEGRATED: Final = "refuse.type_never_integrated"
REFUSE_DOWNGRADE: Final = "refuse.not_a_promotion"

#: The ladder, in order. Promotion advances exactly one rung.
LADDER: Final[tuple[TrustState, ...]] = (
    TrustState.QUARANTINED,
    TrustState.OBSERVED,
    TrustState.VERIFIED,
    TrustState.REVIEWED,
    TrustState.INTEGRATED,
)


def next_rung(state: TrustState) -> TrustState | None:
    """The single state a record may advance to, or ``None`` at the top."""
    if state not in LADDER:
        return None
    index = LADDER.index(state)
    return LADDER[index + 1] if index + 1 < len(LADDER) else None


def _verification_events(events: tuple[Event, ...]) -> tuple[Event, ...]:
    return tuple(
        e
        for e in events
        if e.event_type
        in {
            EventType.VERIFICATION_PASSED,
            EventType.VERIFICATION_FAILED,
            EventType.COMMAND_SUCCEEDED,
            EventType.COMMAND_FAILED,
        }
    )


def _normalize_command(command: str) -> str:
    """Collapse whitespace so ``pytest  -q`` and ``pytest -q`` match.

    Deliberately conservative: no flag reordering, no shell parsing. A procedure
    is promoted only when the command that passed is the command being recorded,
    and being clever about equivalence here would let a *different* command claim
    another's evidence.
    """
    return " ".join(command.split())


def can_promote(
    memory: Memory,
    target: TrustState,
    *,
    evidence: tuple[Event, ...],
    actor_source: Source,
    poisoning_threshold: float = 0.5,
) -> Decision:
    """Whether ``memory`` may advance to ``target``.

    Checks run cheapest-and-most-absolute first, so a rejected record is refused
    before any evidence is examined.
    """
    current = memory.trust_state

    # --- Absolute refusals ------------------------------------------------

    if current is TrustState.REJECTED:
        return Decision(
            False,
            REFUSE_REJECTED,
            "rejected is terminal and permanent: rejected work can never become "
            "project truth, though it remains available as negative experience",
        )

    if is_permanent(current):
        return Decision(
            False,
            REFUSE_PERMANENT,
            f"{current} is permanent; resolve supersession by writing a new record",
        )

    if is_terminal(current) and target is not TrustState.INVALIDATED:
        # Only INVALIDATED has a way back, and it needs its own rule.
        if current is TrustState.INVALIDATED:
            return _revalidation(memory, target, evidence=evidence, actor_source=actor_source)
        return Decision(
            False,
            REFUSE_TERMINAL,
            f"{current} is terminal and excluded from promotion",
        )

    if actor_source is Source.AGENT:
        return Decision(
            False,
            REFUSE_AGENT_ACTOR,
            "agents propose; they never promote. The party making a claim is never "
            "the party granting it trust",
        )

    if is_terminal(target):
        return Decision(
            False,
            REFUSE_DOWNGRADE,
            f"{target} is a withdrawal, not a promotion; use invalidate, supersede, or reject",
        )

    if rank(target) <= rank(current):
        return Decision(
            False,
            REFUSE_DOWNGRADE,
            f"{target} is not above {current}",
        )

    expected = next_rung(current)
    if target is not expected:
        return Decision(
            False,
            REFUSE_SKIPPED_RUNG,
            f"cannot go {current} -> {target} in one step; the next rung is {expected}. "
            "Skipping would leave the intervening evidence unrecorded, making the "
            "audit trail true by accident and incomplete in fact",
        )

    if memory.poisoning_risk >= poisoning_threshold:
        return Decision(
            False,
            REFUSE_POISONING,
            f"poisoning risk {memory.poisoning_risk:.2f} is at or above the "
            f"{poisoning_threshold:.2f} threshold",
        )

    # --- Type ceilings ----------------------------------------------------

    if target is TrustState.INTEGRATED and memory.memory_type in NEVER_INTEGRATED:
        return Decision(
            False,
            REFUSE_NEVER_INTEGRATED,
            f"{memory.memory_type} records never reach integrated: "
            + (
                "an episode happened regardless of what landed"
                if memory.memory_type is MemoryType.EPISODIC
                else "a statistic does not land in a commit"
            ),
        )

    ceiling = TYPE_CEILING_WITHOUT_INTEGRATION[memory.memory_type]
    if rank(target) > rank(ceiling) and not memory.is_landed:
        return Decision(
            False,
            REFUSE_TYPE_CEILING,
            f"{memory.memory_type} records cannot exceed {ceiling} without landed "
            f"history (integration_state is {memory.integration_state})",
        )

    # --- Per-rung evidence ------------------------------------------------

    if target is TrustState.OBSERVED:
        return _to_observed(memory, evidence)
    if target is TrustState.VERIFIED:
        return _to_verified(memory, evidence)
    if target is TrustState.REVIEWED:
        return _to_reviewed(memory, evidence)
    if target is TrustState.INTEGRATED:
        return _to_integrated(memory, evidence, actor_source=actor_source)

    return Decision(False, REFUSE_NO_EVIDENCE, f"no rule covers {current} -> {target}")


def _to_observed(memory: Memory, evidence: tuple[Event, ...]) -> Decision:
    """quarantined -> observed: evidence that the record ties to something real."""
    linked = tuple(
        e.event_id for e in evidence if e.run_id or e.task_id or e.attempt_id or e.commit_sha
    )
    if not linked:
        return Decision(
            False,
            REFUSE_NO_EVIDENCE,
            "no event links this record to a real run, task, attempt, or commit",
        )
    return Decision(
        True,
        RULE_QUARANTINE_TO_OBSERVED,
        f"linked to {len(linked)} event(s) from a real run",
        linked,
    )


def _human_decision_events(evidence: tuple[Event, ...]) -> tuple[Event, ...]:
    return tuple(
        e for e in evidence if e.event_type is EventType.HUMAN_DECISION and e.source is Source.HUMAN
    )


def _semantic_authority(memory: Memory, evidence: tuple[Event, ...]) -> tuple[Event, ...]:
    """The evidence that verifies a repository fact.

    ADR-0004 promotes semantic memory "by landed integration, or human decision",
    and that is the whole of it: no command returns whether the project uses uv.
    Requiring a verification event here instead left ``observed -> verified``
    permanently refused for the category, so every projected fact stalled two
    rungs below ``integrated`` — the state ``presentable_as_current_truth``
    requires — and the one category that exists to state current truth could
    never state any.

    The landing has to be this record's own. A record carries a landed
    ``integration_state`` only because the projector matched it to that commit's
    task or branch, so an unrelated integration event handed in as evidence
    proves nothing about it.
    """
    landed = (
        tuple(e for e in evidence if e.event_type is EventType.INTEGRATION_LANDED and e.commit_sha)
        if memory.is_landed
        else ()
    )
    return (*landed, *_human_decision_events(evidence))


def _to_verified(memory: Memory, evidence: tuple[Event, ...]) -> Decision:
    """observed -> verified: a deterministic verification result.

    The interesting cases:

    * **Procedural** needs the *exact* command to match. A procedure promoted on
      a different command's evidence would be a fabricated runbook.
    * **Gotcha** is verified by a *failure*. The evidence is that something broke,
      which is the whole content of the record.
    * **Decision** has nothing to verify — there is no command behind "we chose
      Typer over Click". Its authority is a person, so the human decision event
      is its evidence at every rung. The rungs are still walked and still
      recorded; what differs is what counts as evidence, not whether evidence is
      required (ADR-0005).
    * **Semantic** has nothing to run either: "the project uses uv" is settled by
      what landed or by a person, which is exactly what ADR-0004 says promotes
      the category.
    """
    if memory.memory_type is MemoryType.DECISION:
        human = _human_decision_events(evidence)
        if human:
            return Decision(
                True,
                RULE_DECISION_HUMAN,
                "a recorded human decision is its own evidence; there is no command to verify",
                tuple(e.event_id for e in human),
            )

    if memory.memory_type is MemoryType.SEMANTIC:
        authority = _semantic_authority(memory, evidence)
        if authority:
            return Decision(
                True,
                RULE_SEMANTIC_VERIFIED,
                "a repository fact is settled by landed history or by human "
                "authority; there is no command that returns whether it holds",
                tuple(e.event_id for e in authority),
            )

    verifications = _verification_events(evidence)
    if not verifications:
        return Decision(
            False,
            REFUSE_NO_EVIDENCE,
            "no verification or command-result event supports this record",
        )

    trusted = tuple(
        e for e in verifications if e.source in {Source.KERNEL, Source.ADAPTER, Source.HUMAN}
    )
    if not trusted:
        return Decision(
            False,
            REFUSE_SOURCE_CEILING,
            "verification evidence exists but none of it came from a source trusted "
            "to report a deterministic outcome",
        )

    if memory.memory_type is MemoryType.PROCEDURAL:
        wanted = _normalize_command(str(memory.content.get("command", "")))
        if not wanted:
            return Decision(
                False,
                REFUSE_NO_EVIDENCE,
                "procedural record has no command to match evidence against",
            )
        matching = tuple(
            e
            for e in trusted
            if _normalize_command(str(e.payload.get("command", ""))) == wanted
            and e.event_type in {EventType.VERIFICATION_PASSED, EventType.COMMAND_SUCCEEDED}
        )
        if not matching:
            return Decision(
                False,
                REFUSE_NO_EVIDENCE,
                f"no passing verification for the exact command {wanted!r}",
            )
        return Decision(
            True,
            RULE_PROCEDURAL_VERIFIED,
            f"command {wanted!r} passed verification",
            tuple(e.event_id for e in matching),
        )

    if memory.memory_type is MemoryType.GOTCHA:
        failures = tuple(
            e
            for e in trusted
            if e.event_type in {EventType.VERIFICATION_FAILED, EventType.COMMAND_FAILED}
        )
        if not failures:
            return Decision(
                False,
                REFUSE_NO_EVIDENCE,
                "a gotcha is verified by a failure; no failure evidence is linked",
            )
        return Decision(
            True,
            RULE_GOTCHA_VERIFIED,
            f"deterministic failure recorded by {failures[0].source}",
            tuple(e.event_id for e in failures),
        )

    return Decision(
        True,
        RULE_OBSERVED_TO_VERIFIED,
        f"{len(trusted)} deterministic verification event(s)",
        tuple(e.event_id for e in trusted),
    )


def _to_reviewed(memory: Memory, evidence: tuple[Event, ...]) -> Decision:
    """verified -> reviewed: an approval from someone who is not the author."""
    if memory.memory_type is MemoryType.DECISION:
        human = _human_decision_events(evidence)
        if human:
            # The human who made the decision is the reviewing authority. No
            # independence check applies: there is no separate author to be
            # independent of, because the decision and its authority are the same
            # act.
            return Decision(
                True,
                RULE_DECISION_HUMAN,
                "human authority stands in for independent review on a decision",
                tuple(e.event_id for e in human),
            )

    approvals = tuple(e for e in evidence if e.event_type is EventType.REVIEW_APPROVED)
    if not approvals:
        return Decision(False, REFUSE_NO_EVIDENCE, "no review approval is linked")

    author = (memory.author_agent or "").strip().lower()
    independent = tuple(
        e
        for e in approvals
        if not author
        or (str(e.payload.get("reviewer", e.agent_profile or "")).strip().lower() != author)
    )
    if not independent:
        reviewer = approvals[0].payload.get("reviewer", approvals[0].agent_profile)
        return Decision(
            False,
            REFUSE_SELF_REVIEW,
            f"the only approval came from the record's own author ({reviewer!r}); "
            "independence is required and a self-review never promotes",
        )
    return Decision(
        True,
        RULE_VERIFIED_TO_REVIEWED,
        "approved by an independent reviewer",
        tuple(e.event_id for e in independent),
    )


def _to_integrated(
    memory: Memory, evidence: tuple[Event, ...], *, actor_source: Source
) -> Decision:
    """reviewed -> integrated: it actually landed.

    A human decision is the one case that reaches ``integrated`` on authority
    rather than on a commit — a decision *is* project truth because a human made
    it, not because a test passed.
    """
    if memory.memory_type is MemoryType.DECISION:
        human = tuple(
            e
            for e in evidence
            if e.event_type is EventType.HUMAN_DECISION and e.source is Source.HUMAN
        )
        if human:
            return Decision(
                True,
                RULE_DECISION_HUMAN,
                "recorded human decision carries project authority",
                tuple(e.event_id for e in human),
            )

    landed = tuple(
        e for e in evidence if e.event_type is EventType.INTEGRATION_LANDED and e.commit_sha
    )
    if not landed:
        return Decision(
            False,
            REFUSE_NOT_LANDED,
            "no integration event with a resolvable commit; verification alone is "
            "not enough for current project truth",
        )
    if memory.integration_state not in LANDED_STATES:
        return Decision(
            False,
            REFUSE_NOT_LANDED,
            f"integration_state is {memory.integration_state}, not a landed state",
        )
    return Decision(
        True,
        RULE_REVIEWED_TO_INTEGRATED,
        f"landed in commit {landed[0].commit_sha}",
        tuple(e.event_id for e in landed),
    )


def _revalidation(
    memory: Memory,
    target: TrustState,
    *,
    evidence: tuple[Event, ...],
    actor_source: Source,
) -> Decision:
    """The one narrow path out of ``invalidated``.

    Requires fresh deterministic evidence that the fact holds again — a reverted
    revert, a restored dependency. ``superseded`` and ``rejected`` have no
    equivalent, which is what closes the laundering route from rejected work back
    to trusted truth.
    """
    if actor_source is Source.AGENT:
        return Decision(False, REFUSE_AGENT_ACTOR, "agents cannot revalidate")

    invalid_at = memory.invalid_at
    fresh = tuple(
        e for e in evidence if e.is_evidence and (invalid_at is None or e.recorded_at > invalid_at)
    )
    if not fresh:
        return Decision(
            False,
            REFUSE_NO_EVIDENCE,
            "revalidation requires deterministic evidence recorded after the "
            "invalidation; none was supplied",
        )
    if rank(target) > rank(TrustState.VERIFIED):
        return Decision(
            False,
            REFUSE_SKIPPED_RUNG,
            f"revalidation restores at most verified, not {target}",
        )
    return Decision(
        True,
        RULE_REVALIDATE,
        f"fresh evidence recorded after invalidation restores this to {target}",
        tuple(e.event_id for e in fresh),
    )


def evidence_states(
    evidence: tuple[Event, ...],
) -> tuple[ReviewState, IntegrationState]:
    """Derive review and integration state from a set of events.

    Precedence is deliberate: a rejection outranks an approval, and a revert
    outranks a landing. The pessimistic reading is the safe one — a record that
    was approved and then rejected should not read as approved.
    """
    review = ReviewState.NONE
    for event in evidence:
        if event.event_type is EventType.REVIEW_REJECTED:
            review = ReviewState.REJECTED
            break
        if event.event_type is EventType.REVIEW_CHANGES_REQUESTED:
            review = ReviewState.CHANGES_REQUESTED
        elif event.event_type is EventType.REVIEW_APPROVED and review is ReviewState.NONE:
            review = ReviewState.APPROVED

    integration = IntegrationState.NONE
    for event in evidence:
        if event.event_type is EventType.INTEGRATION_REVERTED:
            integration = IntegrationState.REVERTED
            break
        if event.event_type is EventType.INTEGRATION_LANDED:
            target = str(event.payload.get("target", "run"))
            integration = (
                IntegrationState.ACCEPTED_USER
                if target == "user"
                else IntegrationState.INTEGRATED_RUN
            )
    return review, integration
