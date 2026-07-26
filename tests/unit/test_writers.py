"""Deterministic writers: reviews, decisions, runs, verification, failures.

Every writer is a pure function of its event. The tests below assert that
directly, because it is what makes ``rebuild`` meaningful (ADR-0007).
"""

from __future__ import annotations

import pytest

from provalume.schemas.events import Event, EventType
from provalume.schemas.memories import MemoryType
from provalume.schemas.scope import ScopeLevel
from provalume.schemas.trust import ReviewState, Source, TrustState, VerificationState
from provalume.writers import decisions, failures, reviews, runs, verification


def event(event_type: EventType, **overrides: object) -> Event:
    defaults: dict = {
        "event_type": event_type,
        "project_id": "p",
        "source": Source.KERNEL,
        "payload": {},
        "branch": "main",
        "task_id": "t1",
        "attempt_id": "a1",
        "agent_profile": "agent-A",
        "recorded_at": "2026-07-25T00:00:00.000Z",
    }
    defaults.update(overrides)
    return Event.create(**defaults)  # type: ignore[arg-type]


OBSERVED = TrustState.OBSERVED


# --- reviews ---------------------------------------------------------------


def test_rejection_becomes_a_lesson() -> None:
    lesson = reviews.build_lesson(
        event(
            EventType.REVIEW_REJECTED,
            payload={
                "reviewer": "reviewer-2",
                "finding": "bare except swallows the traceback",
                "subject": "error handling",
            },
        ),
        landing_state=OBSERVED,
    )
    assert lesson is not None
    assert lesson.memory_type is MemoryType.GOTCHA
    assert lesson.review_state is ReviewState.REJECTED
    assert "reviewer-2" in lesson.text
    assert "bare except" in lesson.text
    assert lesson.content["verdict"] == "rejected"


def test_a_rejection_is_not_a_verification_result() -> None:
    """A reviewer's objection must not stand in for command evidence, or review
    evidence would satisfy a verification rung it never earned."""
    lesson = reviews.build_lesson(
        event(EventType.REVIEW_REJECTED, payload={"finding": "no tests"}),
        landing_state=OBSERVED,
    )
    assert lesson is not None
    assert lesson.verification_state is VerificationState.UNKNOWN


def test_a_bare_rejection_with_no_reason_produces_no_lesson() -> None:
    """It teaches nothing; the episodic record still captures that it happened."""
    assert (
        reviews.build_lesson(
            event(EventType.REVIEW_REJECTED, payload={"reviewer": "r"}), landing_state=OBSERVED
        )
        is None
    )


def test_finding_becomes_a_lesson_with_severity() -> None:
    finding = reviews.build_finding(
        event(
            EventType.REVIEW_FINDING,
            payload={
                "finding": "missing input validation",
                "severity": "high",
                "reviewer": "reviewer-2",
            },
        ),
        landing_state=OBSERVED,
    )
    assert finding is not None
    assert finding.memory_type is MemoryType.GOTCHA
    assert "[high]" in finding.text
    assert "reviewer-2" in finding.text


def test_empty_finding_produces_nothing() -> None:
    assert (
        reviews.build_finding(event(EventType.REVIEW_FINDING, payload={}), landing_state=OBSERVED)
        is None
    )


def test_a_later_approval_attaches_as_resolution() -> None:
    lesson = reviews.build_lesson(
        event(EventType.REVIEW_REJECTED, payload={"finding": "no tests", "subject": "auth"}),
        landing_state=OBSERVED,
    )
    assert lesson is not None
    resolved = reviews.attach_approval(
        lesson,
        event(EventType.REVIEW_APPROVED, payload={"reviewer": "reviewer-3", "note": "tests added"}),
    )
    assert resolved.content["resolution"]["reviewer"] == "reviewer-3"
    assert "Later approved by reviewer-3" in resolved.text
    # The rejection happened; the record of it is the point.
    assert resolved.review_state is ReviewState.REJECTED
    assert len(resolved.source_event_ids) == 2


def test_attaching_an_approval_twice_does_not_duplicate_text() -> None:
    lesson = reviews.build_lesson(
        event(EventType.REVIEW_REJECTED, payload={"finding": "x"}), landing_state=OBSERVED
    )
    assert lesson is not None
    approval = event(EventType.REVIEW_APPROVED, payload={"reviewer": "r2"})
    once = reviews.attach_approval(lesson, approval)
    twice = reviews.attach_approval(once, approval)
    assert twice.text.count("Later approved") == 1


@pytest.mark.parametrize(
    ("reviewer", "author", "expected"),
    [
        ("reviewer-2", "agent-A", True),
        ("agent-A", "agent-A", False),
        ("Agent-A", "agent-a", False),
        ("  agent-a  ", "agent-A", False),
        (None, "agent-A", True),
        ("reviewer-2", None, True),
        ("", "agent-A", True),
    ],
)
def test_independence_comparison(reviewer: str | None, author: str | None, expected: bool) -> None:
    assert reviews.is_independent(reviewer=reviewer, author=author) is expected


# --- decisions -------------------------------------------------------------


def test_decision_keeps_its_rejected_alternatives() -> None:
    """The reusable part: a decision that says only what was chosen cannot stop
    an agent re-proposing what was rejected."""
    decision = decisions.build_decision(
        event(
            EventType.HUMAN_DECISION,
            source=Source.HUMAN,
            payload={
                "question": "http client",
                "selected": "httpx",
                "rejected": ["requests", "aiohttp"],
                "rationale": "sync and async from one API",
                "authority": "tech-lead",
                "consequences": "one more dependency",
            },
        ),
        landing_state=OBSERVED,
    )
    assert decision is not None
    assert decision.content["rejected"] == ["requests", "aiohttp"]
    assert decisions.rejected_alternatives(decision) == ("requests", "aiohttp")
    assert "Rejected: requests, aiohttp" in decision.text
    assert "tech-lead" in decision.text


def test_a_human_decision_is_repository_scoped() -> None:
    """Branch-scoping a decision would lose it the moment that branch merged."""
    decision = decisions.build_decision(
        event(EventType.HUMAN_DECISION, source=Source.HUMAN, payload={"selected": "x"}),
        landing_state=OBSERVED,
    )
    assert decision is not None
    assert decision.scope.level is ScopeLevel.REPOSITORY


def test_an_agent_proposed_decision_stays_branch_scoped() -> None:
    decision = decisions.build_decision(
        event(EventType.HUMAN_DECISION, source=Source.AGENT, payload={"selected": "x"}),
        landing_state=TrustState.QUARANTINED,
    )
    assert decision is not None
    assert decision.scope.level is ScopeLevel.BRANCH


def test_a_decision_with_no_selection_produces_nothing() -> None:
    assert (
        decisions.build_decision(
            event(EventType.HUMAN_DECISION, payload={"rationale": "hmm"}),
            landing_state=OBSERVED,
        )
        is None
    )


def test_rejected_alternatives_accepts_a_bare_string() -> None:
    decision = decisions.build_decision(
        event(EventType.HUMAN_DECISION, payload={"selected": "a", "rejected": "b"}),
        landing_state=OBSERVED,
    )
    assert decision is not None
    assert decisions.rejected_alternatives(decision) == ("b",)


# --- runs / performance ----------------------------------------------------


def test_performance_aggregate_counts_outcomes() -> None:
    accumulator = runs.PerformanceAccumulator(project_id="p", repository_id="r")
    for index in range(5):
        accumulator.observe(
            event(
                EventType.ATTEMPT_COMPLETED,
                payload={
                    "outcome": "success" if index < 4 else "failed",
                    "task_category": "migration",
                },
                adapter="claude-code",
                model="opus",
            )
        )
    built = accumulator.build(landing_state=OBSERVED)
    assert len(built) == 1
    content = built[0].content
    assert content["attempts"] == 5
    assert content["successes"] == 4
    assert content["success_rate"] == 0.8
    assert "4/5" in built[0].text


def test_performance_text_states_the_denominator() -> None:
    """80% over five attempts and over five hundred are different claims."""
    text = runs.performance_text(
        agent_profile="agent-A",
        task_category="migration",
        attempts=5,
        successes=4,
        approvals=1,
        verifications=2,
        fallbacks=0,
    )
    assert "4/5" in text
    assert "80%" in text


def test_performance_with_no_attempts_says_so() -> None:
    text = runs.performance_text(
        agent_profile="agent-A",
        task_category="x",
        attempts=0,
        successes=0,
        approvals=0,
        verifications=0,
        fallbacks=0,
    )
    assert "no recorded attempts" in text


def test_performance_is_repository_scoped_not_branch_scoped() -> None:
    accumulator = runs.PerformanceAccumulator(project_id="p", repository_id="r")
    accumulator.observe(event(EventType.ATTEMPT_COMPLETED, payload={"outcome": "success"}))
    built = accumulator.build(landing_state=OBSERVED)
    assert built[0].scope.level is ScopeLevel.REPOSITORY
    assert built[0].scope.branch is None


def test_performance_ignores_events_without_an_agent() -> None:
    accumulator = runs.PerformanceAccumulator(project_id="p")
    accumulator.observe(
        event(EventType.ATTEMPT_COMPLETED, agent_profile=None, payload={"outcome": "ok"})
    )
    assert accumulator.build(landing_state=OBSERVED) == []


def test_performance_output_is_order_independent() -> None:
    """Required for byte-identical rebuilds."""
    events = [
        event(EventType.ATTEMPT_COMPLETED, payload={"outcome": "success", "task_category": "a"}),
        event(EventType.VERIFICATION_PASSED, payload={"task_category": "a"}),
        event(EventType.REVIEW_APPROVED, payload={"task_category": "a"}),
    ]
    first = runs.PerformanceAccumulator(project_id="p")
    for e in events:
        first.observe(e)
    second = runs.PerformanceAccumulator(project_id="p")
    for e in reversed(events):
        second.observe(e)

    a = first.build(landing_state=OBSERVED)
    b = second.build(landing_state=OBSERVED)
    assert [m.content_hash for m in a] == [m.content_hash for m in b]


def test_performance_keys_separate_agents() -> None:
    accumulator = runs.PerformanceAccumulator(project_id="p")
    accumulator.observe(
        event(
            EventType.ATTEMPT_COMPLETED,
            agent_profile="agent-A",
            payload={"outcome": "success", "task_category": "x"},
        )
    )
    accumulator.observe(
        event(
            EventType.ATTEMPT_COMPLETED,
            agent_profile="agent-B",
            payload={"outcome": "failed", "task_category": "x"},
        )
    )
    assert len(accumulator.build(landing_state=OBSERVED)) == 2


def test_run_summary_text() -> None:
    text = runs.run_summary_text(
        event(
            EventType.RUN_COMPLETED,
            run_id="run-1",
            payload={"outcome": "completed", "task_count": 4},
        )
    )
    assert "run-1" in text
    assert "4 task(s)" in text


# --- verification / procedural --------------------------------------------


def test_procedural_requires_a_command() -> None:
    """Inventing one from surrounding metadata would be the LLM-extraction
    behaviour this design refuses."""
    assert (
        verification.build_procedural(
            event(EventType.VERIFICATION_PASSED, payload={"purpose": "tests"}),
            landing_state=OBSERVED,
        )
        is None
    )


def test_procedural_normalises_whitespace_but_keeps_flags() -> None:
    procedure = verification.build_procedural(
        event(
            EventType.VERIFICATION_PASSED,
            payload={"command": "pytest   -n auto  tests/", "purpose": "suite"},
        ),
        landing_state=OBSERVED,
    )
    assert procedure is not None
    assert procedure.content["command"] == "pytest -n auto tests/"
    assert procedure.content["raw_command"] == "pytest   -n auto  tests/"


def test_semantic_requires_a_statement() -> None:
    assert (
        verification.build_semantic(
            event(EventType.FACT_OBSERVED, payload={"subject": "x"}), landing_state=OBSERVED
        )
        is None
    )


def test_episodic_text_names_the_actor_and_place() -> None:
    text = verification.episodic_text(
        event(EventType.VERIFICATION_FAILED, payload={"command": "pytest -q"}, commit_sha="a" * 40)
    )
    assert "verification FAILED" in text
    assert "pytest -q" in text
    assert "agent-A" in text
    assert "main" in text


def test_scope_falls_back_to_repository_without_a_branch() -> None:
    scope = verification.scope_for(event(EventType.VERIFICATION_PASSED, branch=None))
    assert scope.level is ScopeLevel.REPOSITORY


def test_derived_memory_ids_are_stable_and_kind_separated() -> None:
    """One event projects into several records without collision, and a rebuild
    reproduces the identifiers rather than minting new ones."""
    e = event(EventType.VERIFICATION_FAILED, payload={"command": "x"})
    assert verification.derive_memory_id(e.event_id, "gotcha") == verification.derive_memory_id(
        e.event_id, "gotcha"
    )
    assert verification.derive_memory_id(e.event_id, "gotcha") != verification.derive_memory_id(
        e.event_id, "episodic"
    )


# --- failures --------------------------------------------------------------


def test_gotcha_records_the_signature_and_failed_state() -> None:
    gotcha, signature = failures.build_gotcha(
        event(
            EventType.VERIFICATION_FAILED,
            payload={
                "command": "pytest -n auto",
                "error_kind": "test_failure",
                "excerpt": "E TimeoutError: deadlock",
                "exit_code": 1,
            },
        ),
        landing_state=OBSERVED,
    )
    assert gotcha.memory_type is MemoryType.GOTCHA
    assert gotcha.verification_state is VerificationState.FAILED
    assert gotcha.content["failure_signature"] == signature.value
    assert gotcha.content["exit_code"] == 1
    assert signature.short()


def test_merge_occurrence_updates_the_count_and_keeps_the_earliest_validity() -> None:
    first = event(
        EventType.VERIFICATION_FAILED,
        payload={"command": "x", "excerpt": "E boom"},
        recorded_at="2026-07-01T00:00:00.000Z",
    )
    gotcha, _ = failures.build_gotcha(first, landing_state=OBSERVED)
    later = event(
        EventType.VERIFICATION_FAILED,
        payload={"command": "x", "excerpt": "E boom"},
        recorded_at="2026-07-20T00:00:00.000Z",
    )
    merged = failures.merge_occurrence(gotcha, later, 2)
    assert merged.content["occurrences"] == 2
    assert merged.valid_at == "2026-07-01T00:00:00.000Z"
    assert len(merged.source_event_ids) == 2


def test_elevated_warning_wording() -> None:
    gotcha, _ = failures.build_gotcha(
        event(EventType.VERIFICATION_FAILED, payload={"command": "x", "excerpt": "E boom"}),
        landing_state=OBSERVED,
    )
    assert failures.elevated_warning_text(gotcha, 1) == gotcha.text
    assert "twice" in failures.elevated_warning_text(gotcha, 2)
    assert "5 times" in failures.elevated_warning_text(gotcha, 5)


def test_resolution_link_records_what_worked() -> None:
    gotcha, _ = failures.build_gotcha(
        event(
            EventType.VERIFICATION_FAILED,
            payload={"command": "pytest -n auto", "excerpt": "E deadlock"},
        ),
        landing_state=OBSERVED,
    )
    resolved = failures.build_resolution_link(
        gotcha=gotcha,
        resolution_event=event(
            EventType.VERIFICATION_PASSED, payload={"command": "pytest -p no:xdist"}
        ),
    )
    assert resolved.content["resolution"]["command"] == "pytest -p no:xdist"
    assert "What later worked" in resolved.text


def test_command_normalisation_preserves_flags() -> None:
    """`pytest -n auto` and `pytest -p no:xdist` are different procedures."""
    assert failures.normalize_command("pytest  -n   auto") == "pytest -n auto"
    assert failures.normalize_command("pytest -n auto") != failures.normalize_command(
        "pytest -p no:xdist"
    )


def test_error_normalisation_strips_run_varying_detail() -> None:
    normalised = failures.normalize_error(
        "/Users/alice/proj/src/db.py line 42: failed after 30.5s (pid 8412) "
        "0xdeadbeef 2026-07-25T10:00:00Z"
    )
    assert "alice" not in normalised
    assert "8412" not in normalised
    assert "30.5s" not in normalised
    assert "<dur>" in normalised
    assert "<ts>" in normalised


def test_single_digit_exit_codes_stay_distinct() -> None:
    """`exit 1` and `exit 2` are genuinely different failures."""
    a = failures.compute(command="build", error_text="error: exited with code 1")
    b = failures.compute(command="build", error_text="error: exited with code 2")
    assert a.value != b.value
