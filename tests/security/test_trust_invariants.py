"""The trust invariants from TRUST_MODEL.md §6.

These are the assertions that make "verified" mean something. A change that
breaks one of these should fail CI rather than ship, because each corresponds to
a way an attacker gets a false statement labelled as proved.
"""

from __future__ import annotations

import pytest

from provalume.errors import TrustError
from provalume.policy import promotion
from provalume.schemas.events import Event, EventType
from provalume.schemas.memories import Memory, MemoryType
from provalume.schemas.scope import Scope, ScopeLevel
from provalume.schemas.trust import (
    IntegrationState,
    Source,
    TrustState,
    VerificationState,
    within_ceiling,
)
from provalume.sdk.client import Provalume


def memory(**overrides: object) -> Memory:
    defaults: dict = {
        "memory_type": MemoryType.SEMANTIC,
        "text": "a fact",
        "scope": Scope(level=ScopeLevel.BRANCH, project_id="p", branch="main"),
        "source": Source.KERNEL,
    }
    defaults.update(overrides)
    return Memory.create(**defaults)  # type: ignore[arg-type]


def evidence(event_type: EventType, **overrides: object) -> Event:
    defaults: dict = {
        "event_type": event_type,
        "project_id": "p",
        "source": Source.KERNEL,
        "payload": {"command": "pytest -q"},
        "task_id": "t1",
    }
    defaults.update(overrides)
    return Event.create(**defaults)  # type: ignore[arg-type]


# --- 1. A rejected record is never promoted --------------------------------


@pytest.mark.parametrize(
    "target",
    [TrustState.OBSERVED, TrustState.VERIFIED, TrustState.REVIEWED, TrustState.INTEGRATED],
)
def test_rejected_is_never_promoted(target: TrustState) -> None:
    record = memory(trust_state=TrustState.REJECTED)
    decision = promotion.can_promote(
        record,
        target,
        evidence=(evidence(EventType.VERIFICATION_PASSED),),
        actor_source=Source.HUMAN,
    )
    assert not decision.allowed
    assert decision.rule == promotion.REFUSE_REJECTED


def test_superseded_is_never_promoted() -> None:
    record = memory(trust_state=TrustState.SUPERSEDED)
    decision = promotion.can_promote(
        record,
        TrustState.VERIFIED,
        evidence=(evidence(EventType.VERIFICATION_PASSED),),
        actor_source=Source.HUMAN,
    )
    assert not decision.allowed
    assert decision.rule == promotion.REFUSE_PERMANENT


# --- 2. An agent never promotes --------------------------------------------


@pytest.mark.parametrize(
    "state", [TrustState.QUARANTINED, TrustState.OBSERVED, TrustState.VERIFIED]
)
def test_agent_actor_can_never_promote(state: TrustState) -> None:
    record = memory(trust_state=state)
    target = promotion.next_rung(state)
    assert target is not None
    decision = promotion.can_promote(
        record,
        target,
        evidence=(evidence(EventType.VERIFICATION_PASSED),),
        actor_source=Source.AGENT,
    )
    assert not decision.allowed
    assert decision.rule == promotion.REFUSE_AGENT_ACTOR


def test_sdk_refuses_agent_promotion_and_records_the_refusal(pv: Provalume) -> None:
    pv.propose(text="a claim from an agent", agent="agent-A")
    records = pv.memory_records(include_terminal=True, current_only=False, limit=5)
    assert records
    target = records[0]

    with pytest.raises(TrustError):
        pv.promote(
            target.memory_id, TrustState.OBSERVED, actor="agent-A", actor_source=Source.AGENT
        )

    transitions = pv.memories.transitions_for(target.memory_id)
    refusals = [t for t in transitions if not t["allowed"]]
    assert refusals, "the refusal was not recorded — a vanished attempt is what an "
    "attacker wants"
    assert refusals[0]["policy_rule"] == promotion.REFUSE_AGENT_ACTOR


# --- 3. Source ceilings ----------------------------------------------------


@pytest.mark.parametrize(
    ("source", "highest"),
    [
        (Source.AGENT, TrustState.OBSERVED),
        (Source.IMPORT, TrustState.OBSERVED),
        (Source.KERNEL, TrustState.VERIFIED),
        (Source.ADAPTER, TrustState.VERIFIED),
        (Source.HUMAN, TrustState.INTEGRATED),
    ],
)
def test_source_ceilings_hold(source: Source, highest: TrustState) -> None:
    assert within_ceiling(highest, source)
    above = promotion.next_rung(highest)
    if above is not None:
        assert not within_ceiling(above, source)


def test_terminal_states_are_reachable_from_any_source() -> None:
    """Any source may report that something failed. Withdrawal is not a trust
    grant, and refusing an agent the ability to report a failure would suppress
    exactly what gotcha memory exists to capture."""
    for state in (TrustState.INVALIDATED, TrustState.SUPERSEDED, TrustState.REJECTED):
        assert within_ceiling(state, Source.AGENT)


# --- 4. Semantic truth requires landed history -----------------------------


def test_semantic_below_integrated_is_not_current_truth() -> None:
    for state in (TrustState.OBSERVED, TrustState.VERIFIED, TrustState.REVIEWED):
        record = memory(memory_type=MemoryType.SEMANTIC, trust_state=state)
        assert not record.presentable_as_current_truth


def test_semantic_at_integrated_and_landed_is_current_truth() -> None:
    record = memory(
        memory_type=MemoryType.SEMANTIC,
        trust_state=TrustState.INTEGRATED,
        integration_state=IntegrationState.ACCEPTED_USER,
    )
    assert record.presentable_as_current_truth


def test_semantic_integrated_but_reverted_is_not_current_truth() -> None:
    record = memory(
        memory_type=MemoryType.SEMANTIC,
        trust_state=TrustState.INTEGRATED,
        integration_state=IntegrationState.REVERTED,
    )
    assert not record.presentable_as_current_truth


# --- 5. Self-review never promotes -----------------------------------------


def test_self_review_is_refused() -> None:
    record = memory(trust_state=TrustState.VERIFIED, author_agent="agent-A")
    approval = evidence(
        EventType.REVIEW_APPROVED, payload={"reviewer": "agent-A"}, agent_profile="agent-A"
    )
    decision = promotion.can_promote(
        record, TrustState.REVIEWED, evidence=(approval,), actor_source=Source.KERNEL
    )
    assert not decision.allowed
    assert decision.rule == promotion.REFUSE_SELF_REVIEW


def test_independent_review_is_accepted() -> None:
    record = memory(trust_state=TrustState.VERIFIED, author_agent="agent-A")
    approval = evidence(
        EventType.REVIEW_APPROVED,
        payload={"reviewer": "reviewer-2"},
        agent_profile="reviewer-2",
    )
    decision = promotion.can_promote(
        record, TrustState.REVIEWED, evidence=(approval,), actor_source=Source.KERNEL
    )
    assert decision.allowed
    assert decision.rule == promotion.RULE_VERIFIED_TO_REVIEWED


def test_case_and_whitespace_do_not_defeat_the_self_review_check() -> None:
    record = memory(trust_state=TrustState.VERIFIED, author_agent="Agent-A")
    approval = evidence(EventType.REVIEW_APPROVED, payload={"reviewer": "  agent-a  "})
    decision = promotion.can_promote(
        record, TrustState.REVIEWED, evidence=(approval,), actor_source=Source.KERNEL
    )
    assert not decision.allowed, "case or padding must not disguise a self-review"


# --- 6. Rungs are never skipped --------------------------------------------


def test_skipping_a_rung_is_refused() -> None:
    record = memory(trust_state=TrustState.OBSERVED)
    decision = promotion.can_promote(
        record,
        TrustState.INTEGRATED,
        evidence=(evidence(EventType.INTEGRATION_LANDED, commit_sha="a" * 40),),
        actor_source=Source.HUMAN,
    )
    assert not decision.allowed
    assert decision.rule == promotion.REFUSE_SKIPPED_RUNG


def test_downgrade_is_not_a_promotion() -> None:
    record = memory(trust_state=TrustState.VERIFIED)
    decision = promotion.can_promote(
        record, TrustState.OBSERVED, evidence=(), actor_source=Source.HUMAN
    )
    assert not decision.allowed
    assert decision.rule == promotion.REFUSE_DOWNGRADE


# --- 7. Poisoning threshold blocks promotion -------------------------------


def test_high_poisoning_risk_blocks_promotion() -> None:
    record = memory(trust_state=TrustState.QUARANTINED, poisoning_risk=0.8)
    decision = promotion.can_promote(
        record,
        TrustState.OBSERVED,
        evidence=(evidence(EventType.VERIFICATION_PASSED),),
        actor_source=Source.KERNEL,
        poisoning_threshold=0.5,
    )
    assert not decision.allowed
    assert decision.rule == promotion.REFUSE_POISONING


# --- 8. Type-specific evidence ---------------------------------------------


def test_procedural_requires_the_exact_command() -> None:
    record = memory(
        memory_type=MemoryType.PROCEDURAL,
        trust_state=TrustState.OBSERVED,
        content={"command": "pytest -q"},
    )
    wrong = evidence(EventType.VERIFICATION_PASSED, payload={"command": "pytest -n auto"})
    assert not promotion.can_promote(
        record, TrustState.VERIFIED, evidence=(wrong,), actor_source=Source.KERNEL
    ).allowed

    right = evidence(EventType.VERIFICATION_PASSED, payload={"command": "pytest  -q"})
    decision = promotion.can_promote(
        record, TrustState.VERIFIED, evidence=(right,), actor_source=Source.KERNEL
    )
    assert decision.allowed, "whitespace-only differences should still match"
    assert decision.rule == promotion.RULE_PROCEDURAL_VERIFIED


def test_gotcha_is_verified_by_a_failure() -> None:
    """trust_state=verified alongside verification_state=failed is coherent:
    the evidence is real and the evidence is a failure."""
    record = memory(
        memory_type=MemoryType.GOTCHA,
        trust_state=TrustState.OBSERVED,
        verification_state=VerificationState.FAILED,
    )
    decision = promotion.can_promote(
        record,
        TrustState.VERIFIED,
        evidence=(evidence(EventType.VERIFICATION_FAILED),),
        actor_source=Source.KERNEL,
    )
    assert decision.allowed
    assert decision.rule == promotion.RULE_GOTCHA_VERIFIED


def test_episodic_never_reaches_integrated() -> None:
    record = memory(
        memory_type=MemoryType.EPISODIC,
        trust_state=TrustState.REVIEWED,
        integration_state=IntegrationState.ACCEPTED_USER,
    )
    decision = promotion.can_promote(
        record,
        TrustState.INTEGRATED,
        evidence=(evidence(EventType.INTEGRATION_LANDED, commit_sha="a" * 40),),
        actor_source=Source.KERNEL,
    )
    assert not decision.allowed
    assert decision.rule == promotion.REFUSE_NEVER_INTEGRATED


def test_performance_never_reaches_integrated() -> None:
    record = memory(
        memory_type=MemoryType.PERFORMANCE,
        trust_state=TrustState.REVIEWED,
        integration_state=IntegrationState.ACCEPTED_USER,
    )
    assert not promotion.can_promote(
        record,
        TrustState.INTEGRATED,
        evidence=(evidence(EventType.INTEGRATION_LANDED, commit_sha="a" * 40),),
        actor_source=Source.KERNEL,
    ).allowed


def test_integration_without_a_landed_state_is_refused() -> None:
    """Nothing landed, so integrated is out of reach.

    Two rules can catch this and both are correct: the type ceiling fires first
    (procedural cannot exceed verified without landed history) and the
    landed-state check would fire otherwise. What matters is the refusal and that
    it names a rule, not which of the two won the race.
    """
    record = memory(
        memory_type=MemoryType.PROCEDURAL,
        trust_state=TrustState.REVIEWED,
        integration_state=IntegrationState.NONE,
    )
    decision = promotion.can_promote(
        record,
        TrustState.INTEGRATED,
        evidence=(evidence(EventType.INTEGRATION_LANDED, commit_sha="a" * 40),),
        actor_source=Source.KERNEL,
    )
    assert not decision.allowed
    assert decision.rule in {promotion.REFUSE_NOT_LANDED, promotion.REFUSE_TYPE_CEILING}


# --- 9. Revalidation is narrow ---------------------------------------------


def test_invalidated_can_revalidate_with_fresh_evidence() -> None:
    record = memory(
        trust_state=TrustState.INVALIDATED,
        invalid_at="2026-01-01T00:00:00.000Z",
    )
    fresh = evidence(EventType.VERIFICATION_PASSED, recorded_at="2026-06-01T00:00:00.000Z")
    decision = promotion.can_promote(
        record, TrustState.VERIFIED, evidence=(fresh,), actor_source=Source.HUMAN
    )
    assert decision.allowed
    assert decision.rule == promotion.RULE_REVALIDATE


def test_revalidation_requires_evidence_recorded_after_the_invalidation() -> None:
    record = memory(
        trust_state=TrustState.INVALIDATED,
        invalid_at="2026-06-01T00:00:00.000Z",
    )
    stale = evidence(EventType.VERIFICATION_PASSED, recorded_at="2026-01-01T00:00:00.000Z")
    assert not promotion.can_promote(
        record, TrustState.VERIFIED, evidence=(stale,), actor_source=Source.HUMAN
    ).allowed


def test_revalidation_cannot_exceed_verified() -> None:
    record = memory(trust_state=TrustState.INVALIDATED, invalid_at="2026-01-01T00:00:00.000Z")
    fresh = evidence(
        EventType.INTEGRATION_LANDED,
        commit_sha="a" * 40,
        recorded_at="2026-06-01T00:00:00.000Z",
    )
    assert not promotion.can_promote(
        record, TrustState.INTEGRATED, evidence=(fresh,), actor_source=Source.HUMAN
    ).allowed


# --- 10. Every decision names a rule ---------------------------------------


def test_every_decision_names_a_policy_rule() -> None:
    """The field that makes the trust model falsifiable rather than decorative."""
    cases = [
        (memory(trust_state=TrustState.REJECTED), TrustState.OBSERVED, Source.HUMAN),
        (memory(trust_state=TrustState.OBSERVED), TrustState.INTEGRATED, Source.HUMAN),
        (memory(trust_state=TrustState.OBSERVED), TrustState.VERIFIED, Source.AGENT),
        (memory(trust_state=TrustState.OBSERVED), TrustState.VERIFIED, Source.KERNEL),
    ]
    for record, target, source in cases:
        decision = promotion.can_promote(record, target, evidence=(), actor_source=source)
        assert decision.rule, f"a decision without a rule: {record.trust_state}->{target}"
