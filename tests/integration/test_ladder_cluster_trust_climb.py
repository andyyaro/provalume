"""The top rungs of the ladder, reached the way a real run reaches them.

Three defects lived here at once and none of them had a test, because every
existing assertion about promotion either hand-built a record already in the
state under test or stopped at ``verified``:

* semantic memory could never pass ``observed`` — nothing attaches a
  *verification* event to a fact, so the one category that exists to state
  current truth could never be presented as current truth;
* a self-approval recorded before an independent one hid the independent one
  from the evidence list, capping the record at ``verified`` — a promotion
  denial an agent could trigger against its own work;
* the review verdict is stamped by *attempt*, and disabling that scope stalled
  every claim at ``verified`` while the whole suite and all eval scenarios
  stayed green.

Each test drives the public SDK and asserts the trust state a reader would be
served, not the internals that produce it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from provalume.schemas.memories import Memory, MemoryFilter, MemoryType
from provalume.schemas.trust import ReviewState, TrustState

if TYPE_CHECKING:
    from provalume.sdk.client import Provalume

AUTHOR = "agent-A"
REVIEWER = "reviewer-2"


def _only(pv: Provalume, memory_type: MemoryType) -> Memory:
    found = pv.memory_records(
        MemoryFilter(
            project_id=pv.project_id,
            memory_types=(memory_type,),
            include_terminal=True,
            current_only=False,
            limit=10,
        )
    )
    assert found, f"no {memory_type.value} record was projected"
    assert len(found) == 1, f"expected one {memory_type.value} record, got {len(found)}"
    return found[0]


def _land_a_procedure(pv: Provalume, *, reviewers: tuple[str, ...]) -> None:
    pv.record_verification(
        command="make release",
        passed=True,
        purpose="the release gate",
        task_id="t1",
        attempt_id="a1",
        branch="feat/x",
        agent_profile=AUTHOR,
    )
    for reviewer in reviewers:
        pv.record_review(
            reviewer=reviewer,
            approved=True,
            task_id="t1",
            attempt_id="a1",
            branch="feat/x",
        )
    pv.record_integration(commit_sha="a1b2c3d4", target="user", task_id="t1", branch="feat/x")


def test_a_landed_fact_becomes_current_truth(pv: Provalume) -> None:
    """ADR-0004: semantic memory is promoted by landed integration."""
    pv.record_fact(
        statement="The project uses uv for packaging.",
        subject="package manager",
        task_id="t1",
        attempt_id="a1",
        branch="feat/x",
        agent_profile=AUTHOR,
    )
    pv.record_review(
        reviewer=REVIEWER, approved=True, task_id="t1", attempt_id="a1", branch="feat/x"
    )
    pv.record_integration(commit_sha="a1b2c3d4", target="user", task_id="t1", branch="feat/x")

    fact = _only(pv, MemoryType.SEMANTIC)
    assert fact.trust_state is TrustState.INTEGRATED, (
        f"a landed, independently approved fact is served at {fact.trust_state.value}"
    )
    assert fact.presentable_as_current_truth


def test_a_fact_that_never_landed_is_not_current_truth(pv: Provalume) -> None:
    """The negative twin: authority comes from the landing, not from the review."""
    pv.record_fact(
        statement="The project uses uv for packaging.",
        subject="package manager",
        task_id="t1",
        attempt_id="a1",
        branch="feat/x",
        agent_profile=AUTHOR,
    )
    pv.record_review(
        reviewer=REVIEWER, approved=True, task_id="t1", attempt_id="a1", branch="feat/x"
    )

    fact = _only(pv, MemoryType.SEMANTIC)
    assert fact.trust_state is TrustState.OBSERVED
    assert not fact.presentable_as_current_truth


def test_an_earlier_self_approval_does_not_block_an_independent_one(pv: Provalume) -> None:
    """The author approving first must not cap its own work at `verified`."""
    _land_a_procedure(pv, reviewers=(AUTHOR, REVIEWER))

    procedure = _only(pv, MemoryType.PROCEDURAL)
    assert procedure.trust_state is TrustState.INTEGRATED, (
        "a self-approval recorded before an independent one stalled the record at "
        f"{procedure.trust_state.value}"
    )


def test_a_self_approval_alone_still_refuses_the_rung(pv: Provalume) -> None:
    """Linking every approval must not weaken the independence requirement."""
    _land_a_procedure(pv, reviewers=(AUTHOR,))

    procedure = _only(pv, MemoryType.PROCEDURAL)
    assert procedure.trust_state is TrustState.VERIFIED
    assert procedure.review_state is ReviewState.APPROVED
    refused = [
        dict(t)["policy_rule"]
        for t in pv.memories.transitions_for(procedure.memory_id)
        if not dict(t)["allowed"]
    ]
    assert "refuse.self_review" in refused, (
        f"expected a recorded self-review refusal, got {refused}"
    )


def test_an_approval_alone_does_not_grant_the_reviewed_rung(pv: Provalume) -> None:
    """What `docs/reference/LIFECYCLE.md` step 4 says happens, and does not.

    A procedure cannot pass `verified` without landed history, so the approval is
    stamped and kept as evidence; the rung is granted at the landing. The doc
    claimed the rung here, which no projection path grants and the type ceiling
    would refuse.
    """
    pv.record_verification(
        command="make release",
        passed=True,
        purpose="the release gate",
        task_id="t1",
        attempt_id="a1",
        branch="feat/x",
        agent_profile=AUTHOR,
    )
    pv.record_review(
        reviewer=REVIEWER, approved=True, task_id="t1", attempt_id="a1", branch="feat/x"
    )

    procedure = _only(pv, MemoryType.PROCEDURAL)
    assert procedure.trust_state is TrustState.VERIFIED
    assert procedure.review_state is ReviewState.APPROVED


def test_a_verdict_reaches_the_records_of_its_own_attempt(pv: Provalume) -> None:
    """Attempt-scoped stamping is what carries the verdict to the claim."""
    _land_a_procedure(pv, reviewers=(REVIEWER,))

    procedure = _only(pv, MemoryType.PROCEDURAL)
    assert procedure.trust_state is TrustState.INTEGRATED, (
        "the review verdict never reached the attempt's own record; it is served at "
        f"{procedure.trust_state.value}"
    )
    assert procedure.review_state is ReviewState.APPROVED


def test_a_verdict_on_a_different_attempt_does_not_promote(pv: Provalume) -> None:
    """The negative twin: a verdict about other work is not this record's evidence."""
    pv.record_verification(
        command="make release",
        passed=True,
        purpose="the release gate",
        task_id="t1",
        attempt_id="a1",
        branch="feat/x",
        agent_profile=AUTHOR,
    )
    pv.record_review(
        reviewer=REVIEWER,
        approved=True,
        task_id="t1",
        attempt_id="a2",
        branch="feat/x",
    )
    pv.record_integration(commit_sha="a1b2c3d4", target="user", task_id="t1", branch="feat/x")

    procedure = _only(pv, MemoryType.PROCEDURAL)
    assert procedure.trust_state is TrustState.VERIFIED, (
        "a verdict recorded against another attempt promoted this one to "
        f"{procedure.trust_state.value}"
    )
    assert procedure.review_state is ReviewState.NONE


def test_the_climb_is_identical_after_a_rebuild(pv: Provalume) -> None:
    """Same journal, same ladder: the rungs are not a live-path accident."""
    _land_a_procedure(pv, reviewers=(AUTHOR, REVIEWER))
    live = _only(pv, MemoryType.PROCEDURAL)

    pv.rebuild()
    rebuilt = _only(pv, MemoryType.PROCEDURAL)

    assert rebuilt.trust_state is live.trust_state is TrustState.INTEGRATED
    assert rebuilt.content_hash == live.content_hash
