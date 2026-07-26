"""Audit findings, repository operations, and projection edge cases."""

from __future__ import annotations

import pytest

from provalume.policy import invalidation
from provalume.policy import scope as scope_policy
from provalume.schemas.memories import MemoryFilter, MemoryType
from provalume.schemas.scope import Applicability, Scope, ScopeLevel, specificity
from provalume.schemas.trust import Source, TrustState
from provalume.sdk.client import Provalume
from provalume.store.db import Database
from provalume.store.integrity import Severity
from provalume.store.repository import MemoryRepository


@pytest.fixture
def busy(pv: Provalume) -> Provalume:
    pv.record_verification(
        command="pytest -q", passed=False, excerpt="E boom", error_kind="test_failure", task_id="t1"
    )
    pv.record_verification(command="pytest -q tests", passed=True, task_id="t1")
    pv.record_decision(selected="a", rejected=["b"])
    pv.record_fact(subject="pm", statement="Uses uv.")
    return pv


# --- Audit -----------------------------------------------------------------


def test_a_healthy_database_passes_every_check(busy: Provalume) -> None:
    report = busy.audit()
    assert report.ok
    assert not report.warnings
    assert len(report.checks_run) >= 10
    assert report.summary().endswith("checks passed.")


def test_audit_detects_missing_append_only_triggers(busy: Provalume) -> None:
    """Their absence means a database that looks normal and is silently mutable."""
    busy.db.execute("DROP TRIGGER events_no_update")
    report = busy.audit()
    assert not report.ok
    finding = next(f for f in report.errors if f.check == "append_only_triggers")
    assert "not protected" in finding.message


def test_audit_detects_a_broken_chain(busy: Provalume) -> None:
    busy.db.execute("DROP TRIGGER events_no_update")
    busy.db.execute("UPDATE events SET payload = '{\"x\":1}' WHERE seq = 1")
    report = busy.audit()
    assert any(f.check == "event_chain" for f in report.errors)


def test_audit_detects_an_edited_memory_row(busy: Provalume) -> None:
    """Memories are projections and therefore mutable; the content hash is what
    makes a direct edit visible."""
    memory = busy.memory_records(limit=1)[0]
    with busy.db.tx() as conn:
        conn.execute(
            "UPDATE memories SET text = 'silently rewritten' WHERE memory_id = ?",
            (memory.memory_id,),
        )
    report = busy.audit()
    assert any(f.check == "projection_consistency" for f in report.errors)


def test_audit_detects_unresolvable_provenance(busy: Provalume) -> None:
    memory = busy.memory_records(limit=1)[0]
    with busy.db.tx() as conn:
        conn.execute(
            "UPDATE memories SET source_event_ids = '[\"GHOST\"]' WHERE memory_id = ?",
            (memory.memory_id,),
        )
    report = busy.audit()
    finding = next(f for f in report.errors if f.check == "provenance")
    assert "absent from the journal" in finding.message


def test_audit_warns_about_an_orphaned_supersession(busy: Provalume) -> None:
    memory = busy.memory_records(limit=1)[0]
    busy.db.execute("PRAGMA foreign_keys=OFF")
    with busy.db.tx() as conn:
        conn.execute(
            "UPDATE memories SET supersedes_id = 'GHOST' WHERE memory_id = ?",
            (memory.memory_id,),
        )
    busy.db.execute("PRAGMA foreign_keys=ON")
    report = busy.audit()
    assert any(f.check == "supersession_chains" for f in report.warnings)


def test_audit_reports_statistics(busy: Provalume) -> None:
    stats = busy.audit().stats
    assert stats["events"] > 0
    assert stats["memories_by_trust"]
    assert stats["memories_by_type"]
    assert stats["transitions"] > 0
    assert "refused_transitions" in stats


def test_a_shallow_audit_skips_the_credential_scan(busy: Provalume) -> None:
    assert "credential_scan" not in busy.audit(deep=False).checks_run
    assert "credential_scan" in busy.audit(deep=True).checks_run


def test_findings_render_readably(busy: Provalume) -> None:
    finding = busy.audit().findings[0]
    assert str(finding).startswith("[")
    assert finding.check in str(finding)


def test_severity_ordering_is_meaningful(busy: Provalume) -> None:
    report = busy.audit()
    assert all(f.severity is Severity.INFO for f in report.findings) or report.errors


# --- Repository ------------------------------------------------------------


def test_transitions_record_the_rule_and_evidence(busy: Provalume) -> None:
    procedures = busy.memory_records(memory_types=[MemoryType.PROCEDURAL], limit=5)
    transitions = busy.memories.transitions_for(procedures[0].memory_id)
    assert transitions
    assert all(t["policy_rule"] for t in transitions)
    assert any(t["evidence_event_ids"] for t in transitions)


def test_refusals_are_queryable(pv: Provalume) -> None:
    """A cluster of refusals is a security signal."""
    from provalume.errors import TrustError

    pv.propose(text="a claim", agent="agent-A")
    memory = pv.memory_records(include_terminal=True, current_only=False, limit=5)[0]
    with pytest.raises(TrustError):
        pv.promote(
            memory.memory_id, TrustState.OBSERVED, actor="agent-A", actor_source=Source.AGENT
        )
    refusals = pv.memories.refusals()
    assert refusals
    assert refusals[0]["policy_rule"].startswith("refuse.")


def test_links_are_traversable_in_both_directions(repository: MemoryRepository) -> None:
    repository.add_link(project_id="p", from_id="A", to_id="B", link_type="resolved_by")
    assert repository.links_from("A", link_type="resolved_by")[0]["to_id"] == "B"
    assert repository.links_to("B", link_type="resolved_by")[0]["from_id"] == "A"
    assert repository.links_from("A")
    assert repository.links_to("B")


def test_adding_the_same_link_twice_is_idempotent(repository: MemoryRepository) -> None:
    for _ in range(3):
        repository.add_link(project_id="p", from_id="A", to_id="B", link_type="x")
    assert len(repository.links_from("A")) == 1


def test_contradiction_pairs_are_order_independent(repository: MemoryRepository) -> None:
    repository.add_contradiction(project_id="p", subject_key="s", memory_id_a="B", memory_id_b="A")
    repository.add_contradiction(project_id="p", subject_key="s", memory_id_a="A", memory_id_b="B")
    assert len(repository.contradictions("p")) == 1
    assert repository.contradicted_ids("p") == {"A", "B"}


def test_signature_occurrences_accumulate(repository: MemoryRepository) -> None:
    for expected in (1, 2, 3):
        count = repository.record_signature(
            signature="sig",
            project_id="p",
            memory_id="m",
            command="cmd",
            error_kind="e",
            when="2026-07-25T00:00:00.000Z",
        )
        assert count == expected
    assert repository.signature_rows("p", "sig")[0]["occurrences"] == 3


def test_signature_resolution_is_recorded(repository: MemoryRepository) -> None:
    repository.record_signature(
        signature="sig",
        project_id="p",
        memory_id="m",
        command="c",
        error_kind="e",
        when="2026-07-25T00:00:00.000Z",
    )
    repository.set_signature_resolution(project_id="p", signature="sig", resolved_by_id="E1")
    assert repository.signature_rows("p", "sig")[0]["resolved_by_id"] == "E1"


def test_lookup_by_content_hash_finds_an_identical_record(busy: Provalume) -> None:
    """How a rebuild recognises what it already has."""
    memory = busy.memory_records(limit=1)[0]
    found = busy.memories.by_content_hash(busy.project_id, memory.content_hash)
    assert found is not None
    assert found.memory_id == memory.memory_id


def test_counting_respects_the_filter(busy: Provalume) -> None:
    total = busy.memories.count(
        MemoryFilter(project_id=busy.project_id, include_terminal=True, current_only=False)
    )
    gotchas = busy.memories.count(
        MemoryFilter(
            project_id=busy.project_id,
            memory_types=(MemoryType.GOTCHA,),
            include_terminal=True,
            current_only=False,
        )
    )
    assert 0 < gotchas < total


def test_get_many_with_no_ids_returns_nothing(repository: MemoryRepository) -> None:
    assert repository.get_many(()) == []


def test_projection_watermark_advances(busy: Provalume) -> None:
    assert busy.memories.projection_seq() == busy.journal.latest_seq()


# --- Scope -----------------------------------------------------------------


def test_scope_widening_drops_narrower_fields() -> None:
    """A record promoted to repository scope must not keep claiming a branch."""
    narrow = Scope(
        level=ScopeLevel.BRANCH,
        project_id="p",
        repository_id="r",
        branch="feature/x",
        run_id="run1",
        task_id="t1",
    )
    wide = narrow.widened_to(ScopeLevel.PROJECT)
    assert wide.branch is None
    assert wide.run_id is None
    assert wide.project_id == "p"


def test_widening_to_a_narrower_level_is_refused() -> None:
    wide = Scope(level=ScopeLevel.PROJECT, project_id="p")
    with pytest.raises(ValueError, match="narrower"):
        wide.widened_to(ScopeLevel.BRANCH)


def test_scope_describe_is_readable() -> None:
    described = Scope(
        level=ScopeLevel.BRANCH,
        project_id="p",
        repository_id="r",
        branch="main",
        run_id="run1",
        task_id="t1",
        agent_profile="agent-A",
    ).describe()
    assert "project=p" in described
    assert "branch=main" in described


@pytest.mark.parametrize(
    ("record_branch", "query_branch", "expected"),
    [
        ("main", "main", Applicability.CURRENT),
        ("feature/x", "main", Applicability.CROSS_SCOPE),
        (None, "main", Applicability.UNCERTAIN),
        ("main", None, Applicability.UNCERTAIN),
    ],
)
def test_branch_specificity(
    record_branch: str | None, query_branch: str | None, expected: Applicability
) -> None:
    record = Scope(level=ScopeLevel.BRANCH, project_id="p", branch=record_branch)
    query = Scope(level=ScopeLevel.BRANCH, project_id="p", branch=query_branch)
    _, applicability = specificity(record, query)
    assert applicability is expected


def test_a_different_project_is_maximally_distant() -> None:
    """Callers must already have filtered on project; scoring it rather than
    silently accepting means a missing filter shows as a wrong answer."""
    score, applicability = specificity(
        Scope(level=ScopeLevel.BRANCH, project_id="a"),
        Scope(level=ScopeLevel.BRANCH, project_id="b"),
    )
    assert applicability is Applicability.CROSS_SCOPE
    assert score < 0.5


def test_global_scope_is_unreachable(busy: Provalume) -> None:
    """ADR-0016: the capability does not exist, rather than being disabled."""
    memory = busy.memory_records(limit=1)[0]
    decision = scope_policy.can_widen(memory, ScopeLevel.GLOBAL, actor_source=Source.HUMAN)
    assert not decision.allowed
    assert decision.rule == scope_policy.REFUSE_GLOBAL
    assert "not implemented in 0.1.0" in decision.reason


def test_widening_to_repository_requires_landed_history(busy: Provalume) -> None:
    # Explicitly branch-scoped: the fixture runs without Git, so its records are
    # already repository-scoped and widening them would be a no-op.
    memory = busy.memory_records(memory_types=[MemoryType.GOTCHA], limit=1)[0].model_copy(
        update={"scope": Scope(level=ScopeLevel.BRANCH, project_id="p", branch="x")}
    )
    decision = scope_policy.can_widen(memory, ScopeLevel.REPOSITORY, actor_source=Source.HUMAN)
    assert not decision.allowed
    assert decision.rule == scope_policy.REFUSE_NOT_LANDED


def test_widening_to_project_requires_a_human(busy: Provalume) -> None:
    from provalume.schemas.trust import IntegrationState

    memory = busy.memory_records(limit=1)[0].model_copy(
        update={
            "integration_state": IntegrationState.ACCEPTED_USER,
            "scope": Scope(level=ScopeLevel.REPOSITORY, project_id="p"),
        }
    )
    assert not scope_policy.can_widen(
        memory, ScopeLevel.PROJECT, actor_source=Source.KERNEL
    ).allowed
    assert scope_policy.can_widen(memory, ScopeLevel.PROJECT, actor_source=Source.HUMAN).allowed


def test_agents_cannot_widen_scope(busy: Provalume) -> None:
    memory = busy.memory_records(limit=1)[0].model_copy(
        update={"scope": Scope(level=ScopeLevel.BRANCH, project_id="p", branch="x")}
    )
    decision = scope_policy.can_widen(memory, ScopeLevel.REPOSITORY, actor_source=Source.AGENT)
    assert not decision.allowed
    assert decision.rule == scope_policy.REFUSE_AGENT


# --- Subject keys and chains ------------------------------------------------


def test_subject_keys_are_order_and_case_insensitive() -> None:
    assert invalidation.subject_key("The Package Manager") == invalidation.subject_key(
        "manager package the"
    )


def test_a_short_subject_still_produces_a_key() -> None:
    """`ci`, `pm`, `db` are real subjects; an empty key would silently switch off
    supersession and contradiction detection for them."""
    assert invalidation.subject_key("ci") != ""
    assert invalidation.subject_key("pm") != ""


def test_an_empty_subject_produces_an_empty_key() -> None:
    assert invalidation.subject_key("") == ""
    assert invalidation.subject_key("the of a") == ""


def test_supersession_chains_are_bounded() -> None:
    """A corrupt chain must degrade a query, not hang it."""
    cyclic = {"A": "B", "B": "C", "C": "A"}
    path, truncated = invalidation.walk_supersession("A", cyclic)
    assert truncated
    assert len(path) <= invalidation.MAX_CHAIN_DEPTH + 1


def test_a_linear_chain_walks_to_its_head() -> None:
    path, truncated = invalidation.walk_supersession("A", {"A": "B", "B": "C"})
    assert path == ["A", "B", "C"]
    assert not truncated


def test_supersession_across_types_is_refused(busy: Provalume) -> None:
    gotcha = busy.memory_records(memory_types=[MemoryType.GOTCHA], limit=1)[0]
    fact = busy.memory_records(memory_types=[MemoryType.SEMANTIC], limit=1)[0]
    decision = invalidation.can_supersede(gotcha, fact)
    assert not decision.allowed
    assert decision.rule == invalidation.REFUSE_TYPE_MISMATCH


def test_self_supersession_is_refused(busy: Provalume) -> None:
    memory = busy.memory_records(limit=1)[0]
    assert not invalidation.can_supersede(memory, memory).allowed


def test_contradiction_requires_the_same_branch(busy: Provalume) -> None:
    """Two branches disagreeing is the concurrent-worktree case, not a conflict."""
    a = busy.memory_records(memory_types=[MemoryType.SEMANTIC], limit=1)[0]
    b = a.model_copy(
        update={
            "memory_id": "OTHER",
            "content_hash": "different",
            "scope": a.scope.model_copy(update={"branch": "feature/x"}),
        }
    )
    assert not invalidation.contradicts(a, b)


def test_contradiction_requires_differing_content(busy: Provalume) -> None:
    a = busy.memory_records(memory_types=[MemoryType.SEMANTIC], limit=1)[0]
    identical = a.model_copy(update={"memory_id": "OTHER"})
    assert not invalidation.contradicts(a, identical)


# --- Database --------------------------------------------------------------


def test_read_only_databases_refuse_writes(tmp_path) -> None:
    from provalume.errors import StoreError
    from provalume.store.db import open_database

    path = tmp_path / "ro.db"
    open_database(path).close()
    read_only = open_database(path, read_only=True)
    with pytest.raises(StoreError, match="read-only"), read_only.tx():
        pass
    read_only.close()


def test_database_context_manager_closes(tmp_path) -> None:
    from provalume.store.db import open_database

    with open_database(tmp_path / "ctx.db") as database:
        assert database.schema_version() > 0


def test_vacuum_inside_a_transaction_is_refused(file_db: Database) -> None:
    from provalume.errors import StoreError

    with file_db.tx(), pytest.raises(StoreError, match="VACUUM"):
        file_db.vacuum()


def test_scalar_returns_none_for_no_rows(db: Database) -> None:
    assert db.scalar("SELECT memory_id FROM memories WHERE memory_id = 'nope'") is None
