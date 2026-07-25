"""The retrieval engine, the preflight gate, and the vector path end to end.

The governing property under test: **filters authorise, scoring only reorders.**
Nothing scored can appear that filtering did not admit, and no score can promote
a record past a filter.
"""

from __future__ import annotations

import pytest

from provalume.retrieval.lexical import RetrievalEngine, types_for_intent
from provalume.retrieval.preflight import PreflightGate
from provalume.retrieval.vectors import HashingEmbedder, VectorIndex
from provalume.schemas.memories import MemoryType
from provalume.schemas.provenance import ResolutionStatus
from provalume.schemas.retrieval import RankingPolicy, RecallQuery
from provalume.schemas.trust import TrustState
from provalume.sdk.client import Provalume
from provalume.store.repository import MemoryRepository


@pytest.fixture
def seeded(pv: Provalume) -> Provalume:
    pv.record_verification(
        command="pytest -n auto tests/integration", passed=False,
        excerpt="E TimeoutError: deadlock in db fixture", error_kind="test_failure",
        purpose="the integration suite", task_id="t1", agent_profile="agent-A",
    )
    pv.record_verification(
        command="pytest -p no:xdist tests/integration", passed=True,
        purpose="the integration suite", task_id="t1", agent_profile="agent-A",
    )
    pv.record_decision(
        selected="run integration serially", rejected=["pytest-xdist", "-n auto"],
        rationale="the db fixture is not parallel-safe", authority="tech-lead",
    )
    pv.record_fact(subject="package manager", statement="The project uses uv.")
    return pv


# --- Filters authorise -----------------------------------------------------


def test_project_isolation_has_no_bypass(pv: Provalume) -> None:
    other = Provalume(pv.db, project_id="other", git=None)
    other.record_fact(statement="Project B uses internal-host-42.", subject="host")
    pv.record_fact(statement="Project A uses defaults.", subject="host")
    assert not pv.recall("internal-host-42", limit=20).results


def test_min_trust_floor_excludes_lower_rungs(seeded: Provalume) -> None:
    seeded.propose(text="an unverified claim about integration", agent="agent-X")
    quarantined = seeded.recall("integration", min_trust=TrustState.QUARANTINED, limit=20)
    verified = seeded.recall("integration", min_trust=TrustState.VERIFIED, limit=20)
    assert len(quarantined.results) > len(verified.results)
    assert all(r.trust_state is not TrustState.QUARANTINED for r in verified.results)


def test_terminal_records_are_excluded_by_default(pv: Provalume) -> None:
    pv.record_fact(subject="ci", statement="CI runs on CircleCI.")
    pv.record_fact(subject="ci", statement="CI runs on GitHub Actions.", changed=True)

    default = pv.recall("CircleCI", limit=10).results
    assert not any("CircleCI" in r.text for r in default)

    historical = pv.recall("CircleCI", include_terminal=True, limit=10).results
    assert any("CircleCI" in r.text for r in historical)


def test_type_filter_is_a_nudge_not_an_exclusion(seeded: Provalume) -> None:
    """Asking for gotchas must not hide a decisive fact.

    The nudge is worth 0.2 and is deliberately not decisive: a strongly-matching
    high-trust decision can and should still outrank a weakly-matching gotcha.
    What the filter guarantees is presence of the requested type and *survival*
    of the others, not a strict ordering between them.
    """
    results = seeded.recall("integration serially parallel",
                            memory_types=[MemoryType.GOTCHA], limit=20).results
    kinds = {r.memory_type for r in results}
    assert MemoryType.GOTCHA in kinds, "the requested type was not returned"
    assert kinds - {MemoryType.GOTCHA}, (
        "no other type survived — the type filter excluded rather than nudged"
    )

    # The nudge is visible in the score components even where it does not decide
    # the ordering.
    for result in results:
        expected = 1.0 if result.memory_type is MemoryType.GOTCHA else 0.5
        assert result.explanation.breakdown.type_match == expected


def test_excluded_scopes_are_honoured(pv: Provalume) -> None:
    pv.record_fact(statement="A branch fact.", subject="bf", branch="feature/x")
    without = pv.recall("branch fact", limit=10).results
    engine = RetrievalEngine(pv.db, pv.memories)
    filtered = engine.recall(
        RecallQuery(project_id=pv.project_id, query="branch fact",
                    exclude_scopes=("feature/x",), limit=10)
    )
    assert len(filtered) < len(without) or not filtered


# --- Scoring reorders ------------------------------------------------------


def test_results_are_ranked_and_numbered(seeded: Provalume) -> None:
    results = seeded.recall("integration", limit=10).results
    assert [r.rank for r in results] == list(range(1, len(results) + 1))
    assert results == sorted(results, key=lambda r: -r.score)


def test_a_custom_policy_changes_the_ordering(seeded: Provalume) -> None:
    """The policy is data, so tuning does not mean editing scoring code."""
    engine = RetrievalEngine(seeded.db, seeded.memories,
                             policy=RankingPolicy(w_lex=0.0, w_trust=5.0))
    query = RecallQuery(project_id=seeded.project_id, query="integration", limit=10)
    results = engine.recall(query)
    assert results
    # With lexical zeroed and trust dominant, the highest-trust record leads.
    # Compared by ladder rank, not by name: `verified` sorts after `integrated`
    # alphabetically while ranking below it.
    from provalume.schemas.trust import rank

    assert rank(results[0].trust_state) == max(rank(r.trust_state) for r in results)


def test_retrieval_is_deterministic(seeded: Provalume) -> None:
    first = [r.memory_id for r in seeded.recall("integration", limit=10).results]
    second = [r.memory_id for r in seeded.recall("integration", limit=10).results]
    assert first == second


def test_every_result_explains_itself(seeded: Provalume) -> None:
    """A result that cannot explain itself is a bug, not a cosmetic gap."""
    for result in seeded.recall("integration", limit=10).results:
        assert result.explanation.reasons, f"{result.memory_id} gave no reasons"
        assert result.explanation.filters_passed
        assert result.explanation.breakdown.total == pytest.approx(result.score)


def test_access_counts_are_recorded(seeded: Provalume) -> None:
    first = seeded.recall("integration", limit=3).results
    assert first
    seeded.recall("integration", limit=3)
    memory = seeded.memories.get(first[0].memory_id)
    assert memory is not None
    assert memory.access_count >= 2
    assert memory.last_accessed_at is not None


def test_an_empty_query_browses_rather_than_searches(seeded: Provalume) -> None:
    results = seeded.recall("", limit=20).results
    assert results
    assert all(r.explanation.breakdown.lexical == 0.0 for r in results)


def test_no_match_returns_nothing(seeded: Provalume) -> None:
    assert not seeded.recall("zzzz-nonexistent-token-qqqq", limit=10).results


# --- Provenance ------------------------------------------------------------


def test_provenance_assembles_the_evidence_chain(seeded: Provalume) -> None:
    procedures = seeded.memory_records(memory_types=[MemoryType.PROCEDURAL], limit=5)
    assert procedures
    provenance = seeded.explain(procedures[0].memory_id)
    assert provenance is not None
    assert provenance.verifications
    assert provenance.verifications[0].passed
    assert provenance.describe()


def test_provenance_of_an_unknown_memory_is_none(pv: Provalume) -> None:
    assert pv.explain("NOPE") is None


def test_missing_evidence_events_read_as_broken(pv: Provalume) -> None:
    """A record citing an event that does not exist is the forged-provenance
    signature (threat T15) — not merely unresolvable."""
    pv.record_verification(command="pytest", passed=True, purpose="x")
    memory = pv.memory_records(memory_types=[MemoryType.PROCEDURAL])[0]
    with pv.db.tx() as conn:
        conn.execute(
            "UPDATE memories SET source_event_ids = ? WHERE memory_id = ?",
            ('["GHOST-EVENT-ID"]', memory.memory_id),
        )
    provenance = pv.explain(memory.memory_id)
    assert provenance is not None
    assert provenance.resolution is ResolutionStatus.BROKEN
    assert "absent" in provenance.resolution_detail


def test_decision_provenance_carries_rejected_alternatives(seeded: Provalume) -> None:
    decision = seeded.memory_records(memory_types=[MemoryType.DECISION])[0]
    provenance = seeded.explain(decision.memory_id)
    assert provenance is not None
    assert provenance.decisions
    assert "pytest-xdist" in provenance.decisions[0].rejected


def test_intent_shortcuts_map_to_type_sets() -> None:
    assert MemoryType.GOTCHA in types_for_intent("debugging")
    assert MemoryType.DECISION in types_for_intent("planning")
    assert types_for_intent("nonsense") == ()


# --- Preflight -------------------------------------------------------------


def test_exact_signature_match_is_full_confidence(seeded: Provalume) -> None:
    result = seeded.preflight(
        command="pytest -n auto tests/integration",
        error_kind="test_failure",
        error_text="E TimeoutError: deadlock in db fixture",
    )
    assert result.matched
    assert result.matches[0].confidence >= 0.99
    assert "exact failure signature" in result.matches[0].match_reasons[0]


def test_same_command_different_error_still_warns(seeded: Provalume) -> None:
    result = seeded.preflight(
        command="pytest -n auto tests/integration",
        error_kind="other", error_text="E AssertionError: different",
    )
    assert result.matched
    assert result.matches[0].confidence < 1.0


def test_a_rejected_alternative_produces_a_warning(seeded: Provalume) -> None:
    """Re-proposing something a decision already rejected is a wasted cycle."""
    result = seeded.preflight(command="pytest-xdist run", subsystem="tests")
    assert result.matched
    assert any("already rejected" in r for m in result.matches for r in m.match_reasons)


def test_unrelated_actions_do_not_warn(seeded: Provalume) -> None:
    for command in ("npm run lint", "terraform apply", "cargo fmt"):
        assert not seeded.preflight(command=command, record=False).matched


def test_the_gate_warns_and_does_not_block_by_default(seeded: Provalume) -> None:
    result = seeded.preflight(
        command="pytest -n auto tests/integration", error_kind="test_failure",
        error_text="E TimeoutError: deadlock in db fixture",
    )
    assert not result.should_block
    assert "warning, not a block" in result.summary


def test_blocking_requires_an_explicit_policy_and_repetition(pv: Provalume) -> None:
    excerpt = "E TimeoutError: deadlock"
    for _ in range(2):
        pv.record_verification(command="flaky-cmd", passed=False, excerpt=excerpt,
                               error_kind="test_failure")

    permissive = PreflightGate(pv.memories, allow_blocking=False)
    strict = PreflightGate(pv.memories, allow_blocking=True)
    kwargs = {
        "project_id": pv.project_id,
        "command": "flaky-cmd",
        "error_kind": "test_failure",
        "error_text": excerpt,
    }
    assert not permissive.check(**kwargs).should_block
    assert strict.check(**kwargs).should_block


def test_the_summary_carries_every_documented_field(seeded: Provalume) -> None:
    summary = seeded.preflight(
        command="pytest -n auto tests/integration", error_kind="test_failure",
        error_text="E TimeoutError: deadlock in db fixture",
    ).summary
    for label in ("Previous attempt", "Failure evidence", "What later worked",
                  "Applicability", "Provenance", "Trust state"):
        assert label in summary, f"the warning is missing {label!r}"


def test_multi_line_evidence_is_collapsed_for_the_layout(pv: Provalume) -> None:
    pv.record_verification(
        command="build", passed=False, error_kind="e",
        excerpt="Traceback:\n  File 'a.py'\nValueError: boom\n=== 1 failed ===",
    )
    summary = pv.preflight(command="build", error_kind="e",
                           error_text="ValueError: boom").summary
    evidence_lines = [ln for ln in summary.splitlines() if "Failure evidence" in ln]
    assert len(evidence_lines) == 1
    assert "\n" not in evidence_lines[0]


def test_an_unresolved_trap_says_so(pv: Provalume) -> None:
    pv.record_verification(command="x", passed=False, excerpt="E boom", error_kind="e")
    result = pv.preflight(command="x", error_kind="e", error_text="E boom")
    assert result.matches[0].what_later_worked == ""
    assert "nothing recorded yet" in result.summary


def test_file_overlap_produces_a_weak_match(pv: Provalume) -> None:
    pv.record_verification(command="pytest tests/test_db.py", passed=False,
                           excerpt="E boom", error_kind="e")
    result = pv.preflight(files=("src/test_db.py",), record=False)
    assert result.matched, "file overlap no longer matches"
    assert result.matches[0].confidence <= 0.5
    assert "previously failed touching test_db.py" in result.matches[0].match_reasons


def test_subsystem_overlap_produces_a_weak_match(pv: Provalume) -> None:
    """The companion tier to file overlap, and the one most prone to noise."""
    pv.record_verification(command="terraform apply", passed=False,
                           excerpt="E lock timeout", error_kind="e")
    matched = pv.preflight(subsystem="terraform", record=False)
    assert matched.matched, "subsystem overlap no longer matches"
    assert "previously failed in terraform" in matched.matches[0].match_reasons
    assert matched.matches[0].confidence <= 0.6

    assert not pv.preflight(subsystem="frontend", record=False).matched


# --- Vectors ---------------------------------------------------------------


def test_vectors_cannot_return_an_unauthorised_record(seeded: Provalume) -> None:
    """Threat T6: an adversarial embedding can win similarity and still not be
    returned, because governance filters run first."""
    index = VectorIndex(seeded.db, HashingEmbedder())
    all_ids = [m.memory_id for m in seeded.memory_records(limit=50)]
    index.upsert_many([(m.memory_id, m.text) for m in seeded.memory_records(limit=50)])

    authorised = all_ids[:2]
    hits = index.search("integration deadlock", limit=20, candidate_ids=authorised)
    assert all(mid in authorised for mid, _ in hits)


def test_vector_index_round_trip(seeded: Provalume) -> None:
    index = VectorIndex(seeded.db, HashingEmbedder())
    stored = index.upsert_many(
        [(m.memory_id, m.text) for m in seeded.memory_records(limit=50)]
    )
    assert stored > 0
    assert index.count() == stored
    assert index.search("integration", limit=5)

    index.clear()
    assert index.count() == 0
    assert index.search("integration", limit=5) == []


def test_upserting_the_same_memory_twice_replaces_rather_than_duplicates(
    seeded: Provalume,
) -> None:
    index = VectorIndex(seeded.db, HashingEmbedder())
    memory = seeded.memory_records(limit=1)[0]
    index.upsert(memory.memory_id, memory.text)
    index.upsert(memory.memory_id, memory.text + " revised")
    assert index.count() == 1


def test_searching_an_empty_candidate_set_returns_nothing(seeded: Provalume) -> None:
    index = VectorIndex(seeded.db, HashingEmbedder())
    index.upsert_many([(m.memory_id, m.text) for m in seeded.memory_records(limit=10)])
    assert index.search("anything", candidate_ids=[]) == []


def test_searching_an_empty_index_returns_nothing(pv: Provalume) -> None:
    assert VectorIndex(pv.db, HashingEmbedder()).search("anything") == []


def test_changing_the_model_isolates_the_index(seeded: Provalume) -> None:
    """Distances from two models are not comparable, so they must not mix."""
    small = VectorIndex(seeded.db, HashingEmbedder(dimensions=16))
    large = VectorIndex(seeded.db, HashingEmbedder(dimensions=64))
    memories = [(m.memory_id, m.text) for m in seeded.memory_records(limit=10)]
    small.upsert_many(memories)
    assert small.count() > 0
    assert large.count() == 0, "a different model must not see another model's vectors"


def test_repository_touch_is_a_no_op_for_no_ids(repository: MemoryRepository) -> None:
    repository.touch(())
