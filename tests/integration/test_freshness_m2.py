"""M2 behaviour: freshness derivation, its source gate, and its surfacing.

The derivation table is ADR-0020's; the source gate is the design-lock rule
that only kernel-sourced freshness events derive anything — an imported or
agent-sourced `freshness.triggered` must not be able to relabel a local
record any more than an imported claim can raise its trust (T17, T28).
"""

from __future__ import annotations

from provalume.schemas.events import EventType
from provalume.schemas.freshness import FreshnessState
from provalume.schemas.memories import MemoryType
from provalume.schemas.trust import Source
from provalume.sdk.client import Provalume
from provalume.store.db import Database


def _claim(pv: Provalume) -> str:
    pv.record_verification(command="pytest -q tests/", passed=True)
    return next(
        m.memory_id for m in pv.memory_records(limit=10) if m.memory_type is MemoryType.PROCEDURAL
    )


def _radius(pv: Provalume, record_id: str, *, source: Source = Source.KERNEL) -> None:
    pv.record_event(
        EventType.BLAST_RADIUS_RECORDED,
        source=source,
        payload={
            "record_id": record_id,
            "method": "import_graph",
            "paths": ["mod.py"],
            "tool": "ast",
            "tool_version": "3.12",
        },
    )


def _trigger(pv: Provalume, record_id: str, *, source: Source = Source.KERNEL) -> None:
    pv.record_event(
        EventType.FRESHNESS_TRIGGERED,
        source=source,
        payload={
            "record_id": record_id,
            "trigger_commit": "b" * 40,
            "changed_paths": ["mod.py"],
            "changed_paths_total": 1,
            "intersecting_paths": ["mod.py"],
        },
    )


def _freshness(pv: Provalume, record_id: str) -> FreshnessState:
    memory = pv.memories.get(record_id)
    assert memory is not None
    return memory.freshness


def test_the_derivation_table(pv: Provalume) -> None:
    """radius → current; trigger → suspect; relevant stays; irrelevant
    discharges; re-run passed → current; failed → stale; errored → no
    transition. Live and after a rebuild."""
    record_id = _claim(pv)
    assert _freshness(pv, record_id) is FreshnessState.UNVERIFIABLE

    _radius(pv, record_id)
    assert _freshness(pv, record_id) is FreshnessState.CURRENT

    _trigger(pv, record_id)
    assert _freshness(pv, record_id) is FreshnessState.SUSPECT

    def assess(verdict: str, reason: str) -> None:
        pv.record_event(
            EventType.RELEVANCE_ASSESSED,
            source=Source.KERNEL,
            payload={
                "record_id": record_id,
                "trigger_commit": "b" * 40,
                "verdict": verdict,
                "differ_version": "1",
                "reason_code": reason,
            },
        )

    assess("relevant", "body_changed")
    assert _freshness(pv, record_id) is FreshnessState.SUSPECT
    assess("irrelevant", "comment_only")
    assert _freshness(pv, record_id) is FreshnessState.CURRENT

    _trigger(pv, record_id)

    def rerun(outcome: str) -> None:
        pv.record_event(
            EventType.REVERIFICATION_EXECUTED,
            source=Source.KERNEL,
            payload={
                "record_id": record_id,
                "trigger_commit": "b" * 40,
                "command": "pytest -q tests/",
                "exit_code": 0 if outcome == "passed" else 1,
                "duration_ms": 10,
                "timeout_ms": 60_000,
                "environment_fingerprint": "sha256:" + "c" * 64,
                "outcome": outcome,
            },
        )

    rerun("errored")
    assert _freshness(pv, record_id) is FreshnessState.SUSPECT, (
        "the engine's own failure is never evidence about the record"
    )
    rerun("failed")
    assert _freshness(pv, record_id) is FreshnessState.STALE
    rerun("passed")
    assert _freshness(pv, record_id) is FreshnessState.CURRENT

    before = _freshness(pv, record_id)
    pv.rebuild()
    assert _freshness(pv, record_id) is before


def test_only_kernel_sourced_events_derive_freshness(pv: Provalume) -> None:
    """The D6 source gate: imported and agent-sourced freshness events are
    stored append-only and derive nothing."""
    record_id = _claim(pv)
    _radius(pv, record_id)
    assert _freshness(pv, record_id) is FreshnessState.CURRENT

    _trigger(pv, record_id, source=Source.IMPORT)
    assert _freshness(pv, record_id) is FreshnessState.CURRENT, (
        "an imported trigger must not relabel a local record (T17/T28)"
    )
    _trigger(pv, record_id, source=Source.AGENT)
    assert _freshness(pv, record_id) is FreshnessState.CURRENT

    stored = [e for e in pv.events() if e.event_type == EventType.FRESHNESS_TRIGGERED]
    assert len(stored) == 2, "the gate is derivation-only; the journal keeps everything"

    _trigger(pv, record_id, source=Source.KERNEL)
    assert _freshness(pv, record_id) is FreshnessState.SUSPECT


def test_a_crafted_record_id_cannot_reach_another_project(db: Database) -> None:
    """T9: a freshness event in project B naming project A's record derives
    nothing, even from the kernel."""
    pv_a = Provalume(db, project_id="project-a", git=None)
    pv_b = Provalume(db, project_id="project-b", git=None)
    record_a = _claim(pv_a)
    _radius(pv_a, record_a)
    assert _freshness(pv_a, record_a) is FreshnessState.CURRENT

    _trigger(pv_b, record_a)
    assert _freshness(pv_a, record_a) is FreshnessState.CURRENT


def test_the_digest_label_carries_both_axes(pv: Provalume) -> None:
    """A suspect record reads `[... · SUSPECT]`; a current one is unmarked —
    the axis speaks up only when it has something to say."""
    record_id = _claim(pv)
    _radius(pv, record_id)
    digest = pv.recall("pytest", limit=5).digest(char_budget=2000)
    assert "· SUSPECT" not in digest.text
    assert "· UNVERIFIABLE" in digest.text, "the episodic record has no radius and must say so"

    _trigger(pv, record_id)
    digest = pv.recall("pytest", limit=5).digest(char_budget=2000)
    assert "· SUSPECT" in digest.text
    item = next(i for i in digest.items if i.memory_id == record_id)
    assert item.freshness == "suspect"


def test_recall_and_preflight_carry_freshness(pv: Provalume) -> None:
    pv.record_verification(command="pytest -q tests/", passed=False, excerpt="E boom")
    gotcha = next(m for m in pv.memory_records(limit=10) if m.memory_type is MemoryType.GOTCHA)
    _radius(pv, gotcha.memory_id)
    _trigger(pv, gotcha.memory_id)

    result = next(r for r in pv.recall("boom", limit=5) if r.memory_id == gotcha.memory_id)
    assert result.freshness is FreshnessState.SUSPECT

    match = pv.preflight(command="pytest -q tests/", record=False).matches[0]
    assert match.freshness is FreshnessState.SUSPECT
