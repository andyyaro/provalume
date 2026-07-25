"""End-to-end lifecycle through the SDK: writers, promotion, retrieval, rebuild."""

from __future__ import annotations

import pytest

from provalume.schemas.memories import MemoryType
from provalume.schemas.trust import IntegrationState, ReviewState, TrustState
from provalume.sdk.client import Provalume

FAILING = "pytest -n auto tests/integration"
WORKING = "pytest -p no:xdist tests/integration"
EXCERPT = "E   TimeoutError: deadlock in db fixture teardown"


def test_failure_produces_one_gotcha_with_an_occurrence_count(pv: Provalume) -> None:
    """Repeats fold into one record. Repetition is a count, not a new record."""
    for _ in range(3):
        pv.record_verification(command=FAILING, passed=False, excerpt=EXCERPT,
                               error_kind="test_failure", task_id="t1")

    gotchas = pv.memory_records(memory_types=[MemoryType.GOTCHA], limit=10)
    assert len(gotchas) == 1
    assert gotchas[0].content["occurrences"] == 3
    assert "3 times" in gotchas[0].text


def test_gotcha_is_verified_by_its_failure(pv: Provalume) -> None:
    """The case that forced trust_state and verification_state apart."""
    pv.record_verification(command=FAILING, passed=False, excerpt=EXCERPT,
                           error_kind="test_failure")
    gotcha = pv.memory_records(memory_types=[MemoryType.GOTCHA])[0]
    assert gotcha.trust_state is TrustState.VERIFIED
    assert gotcha.verification_state.value == "failed"


def test_a_later_success_is_linked_as_the_resolution(pv: Provalume) -> None:
    """A different command that achieved the same declared purpose is the fix.

    The shared ``purpose`` is what makes it a link. Without one, two commands in
    one task are only two commands in one task, and inferring a resolution from
    that credits the wrong success.
    """
    pv.record_verification(command=FAILING, passed=False, excerpt=EXCERPT,
                           error_kind="test_failure", purpose="the integration suite",
                           task_id="t1")
    pv.record_verification(command=WORKING, passed=True,
                           purpose="the integration suite", task_id="t1")

    gotcha = pv.memory_records(memory_types=[MemoryType.GOTCHA])[0]
    assert gotcha.content["resolution"] is not None
    assert gotcha.content["resolution"]["command"] == WORKING
    assert "What later worked" in gotcha.text


def test_full_promotion_ladder_is_recorded_rung_by_rung(pv: Provalume) -> None:
    pv.record_verification(command=WORKING, passed=True, purpose="suite",
                           agent_profile="agent-A", task_id="t1")
    procedure = pv.memory_records(memory_types=[MemoryType.PROCEDURAL])[0]
    assert procedure.trust_state is TrustState.VERIFIED

    pv.record_review(reviewer="reviewer-2", approved=True, agent_profile="reviewer-2",
                     task_id="t1")
    pv.record_integration(commit_sha="a" * 40, target="user", task_id="t1")

    promoted = pv.memories.get(procedure.memory_id)
    assert promoted is not None
    assert promoted.trust_state is TrustState.INTEGRATED
    assert promoted.review_state is ReviewState.APPROVED
    assert promoted.integration_state is IntegrationState.ACCEPTED_USER

    rungs = [
        (t["from_state"], t["to_state"])
        for t in reversed(pv.memories.transitions_for(procedure.memory_id))
        if t["allowed"]
    ]
    assert rungs == [
        ("observed", "verified"),
        ("verified", "reviewed"),
        ("reviewed", "integrated"),
    ]


def test_a_review_does_not_approve_the_failure_it_prompted(pv: Provalume) -> None:
    """A reviewer approving a fix has not approved the bug."""
    pv.record_verification(command=FAILING, passed=False, excerpt=EXCERPT,
                           error_kind="test_failure", task_id="t1")
    pv.record_verification(command=WORKING, passed=True, task_id="t1")
    pv.record_review(reviewer="reviewer-2", approved=True, task_id="t1")

    gotcha = pv.memory_records(memory_types=[MemoryType.GOTCHA])[0]
    assert gotcha.review_state is ReviewState.NONE
    assert gotcha.integration_state is IntegrationState.NONE


def test_landing_does_not_integrate_a_failure_record(pv: Provalume) -> None:
    """What lands is the fix; the failure is still a failure."""
    pv.record_verification(command=FAILING, passed=False, excerpt=EXCERPT,
                           error_kind="test_failure", task_id="t1")
    pv.record_integration(commit_sha="b" * 40, target="user", task_id="t1")

    gotcha = pv.memory_records(memory_types=[MemoryType.GOTCHA])[0]
    assert gotcha.trust_state is TrustState.VERIFIED
    assert gotcha.integration_state is IntegrationState.NONE


def test_human_decision_reaches_integrated_on_authority(pv: Provalume) -> None:
    """A decision has no command to verify; its authority is a person."""
    pv.record_decision(selected="use uv", rejected=["pip", "poetry"],
                       rationale="faster and lockfile-native", authority="tech-lead")
    decision = pv.memory_records(memory_types=[MemoryType.DECISION])[0]
    assert decision.trust_state is TrustState.INTEGRATED
    assert decision.content["rejected"] == ["pip", "poetry"]


def test_supersession_retains_both_records(pv: Provalume) -> None:
    pv.record_fact(subject="pm", statement="The project uses pip.")
    pv.record_fact(subject="pm", statement="The project uses uv.", changed=True)

    everything = pv.memory_records(
        memory_types=[MemoryType.SEMANTIC], include_terminal=True, current_only=False
    )
    states = {m.text: m.trust_state for m in everything}
    assert states["The project uses pip."] is TrustState.SUPERSEDED
    assert states["The project uses uv."] is not TrustState.SUPERSEDED

    superseded = next(m for m in everything if m.trust_state is TrustState.SUPERSEDED)
    assert superseded.invalid_at is not None


def test_invalidation_retains_history(pv: Provalume) -> None:
    pv.record_fact(subject="ci", statement="CI runs nightly.")
    fact = pv.memory_records(memory_types=[MemoryType.SEMANTIC])[0]
    pv.invalidate(fact.memory_id, reason="no longer scheduled")

    after = pv.memories.get(fact.memory_id)
    assert after is not None
    assert after.trust_state is TrustState.INVALIDATED
    assert after.invalid_at is not None


def test_rejected_branch_records_become_permanently_non_truth(pv: Provalume) -> None:
    from provalume.schemas.events import EventType
    from provalume.schemas.trust import Source

    pv.record_fact(statement="A flag exists.", subject="flag", branch="feature/x")
    pv.record_event(EventType.BRANCH_REJECTED, source=Source.HUMAN,
                    payload={"branch": "feature/x"}, branch="feature/x")

    records = pv.memory_records(include_terminal=True, current_only=False, limit=20)
    rejected = [m for m in records if m.trust_state is TrustState.REJECTED]
    assert rejected, "the branch rejection did not withdraw its records"
    assert all(not m.presentable_as_current_truth for m in rejected)


def test_rebuild_reproduces_projections_byte_for_byte(pv: Provalume) -> None:
    """ADR-0002's claim in executable form."""
    pv.record_verification(command=FAILING, passed=False, excerpt=EXCERPT,
                           error_kind="test_failure", task_id="t1")
    pv.record_verification(command=WORKING, passed=True, task_id="t1")
    pv.record_review(reviewer="reviewer-2", approved=True, task_id="t1")
    pv.record_integration(commit_sha="c" * 40, target="user", task_id="t1")
    pv.record_decision(selected="serialise", rejected=["parallelise"])
    pv.record_fact(subject="pm", statement="Uses uv.")

    before = {
        m.memory_id: (m.content_hash, m.trust_state, m.text)
        for m in pv.memory_records(include_terminal=True, current_only=False, limit=100)
    }
    pv.rebuild()
    after = {
        m.memory_id: (m.content_hash, m.trust_state, m.text)
        for m in pv.memory_records(include_terminal=True, current_only=False, limit=100)
    }
    assert before == after


def test_writers_are_pure_functions_of_their_event(pv: Provalume) -> None:
    """Same event in, same record out — including the identifier."""
    from provalume.writers.failures import build_gotcha

    event = pv.record_verification(command=FAILING, passed=False, excerpt=EXCERPT,
                                   error_kind="test_failure")
    first, sig1 = build_gotcha(event, landing_state=TrustState.OBSERVED)
    second, sig2 = build_gotcha(event, landing_state=TrustState.OBSERVED)
    assert first.memory_id == second.memory_id
    assert first.content_hash == second.content_hash
    assert sig1.value == sig2.value


def test_status_reports_the_chain_head(pv: Provalume) -> None:
    pv.record_verification(command="x", passed=True)
    status = pv.status()
    assert status["events"] == 1
    assert status["chain_head"].startswith("sha256:")
    assert status["project_id"] == "test-project"


def test_audit_passes_on_a_healthy_database(pv: Provalume) -> None:
    pv.record_verification(command=FAILING, passed=False, excerpt=EXCERPT,
                           error_kind="test_failure")
    pv.record_decision(selected="x")
    report = pv.audit()
    assert report.ok, [str(f) for f in report.errors]
    assert not report.warnings, [str(f) for f in report.warnings]


def test_proposals_never_escape_quarantine_without_evidence(pv: Provalume) -> None:
    pv.propose(text="The project definitely uses bazel.", agent="agent-A")
    pv.rebuild()
    records = pv.memory_records(include_terminal=True, current_only=False, limit=10)
    assert records
    assert all(m.trust_state is TrustState.QUARANTINED for m in records)


def test_preflight_records_a_warning_event(pv: Provalume) -> None:
    pv.record_verification(command=FAILING, passed=False, excerpt=EXCERPT,
                           error_kind="test_failure")
    result = pv.preflight(command=FAILING, error_kind="test_failure", error_text=EXCERPT)
    assert result.matched
    assert result.warning_event_id

    outcome = pv.record_warning_outcome(
        warning_event_id=result.warning_event_id, heeded=True
    )
    assert outcome.causal_parent_event_id == result.warning_event_id


@pytest.mark.parametrize("budget", [400, 800, 2000, 8000])
def test_digest_never_exceeds_its_budget(pv: Provalume, budget: int) -> None:
    for index in range(30):
        pv.record_verification(
            command=f"task-{index} --with-a-long-flag",
            passed=False,
            excerpt=f"E Error: subsystem {index} failed with a long message",
            error_kind="task_error",
        )
    digest = pv.recall("subsystem failed", limit=30).digest(char_budget=budget)
    assert digest.chars_used <= budget
    assert digest.text.startswith("Historical context from Provalume follows.")


def test_digest_refuses_a_budget_too_small_for_the_banner(pv: Provalume) -> None:
    from provalume.errors import BudgetExceeded

    pv.record_verification(command="x", passed=True)
    with pytest.raises(BudgetExceeded, match="banner"):
        pv.recall("x").digest(char_budget=50)
