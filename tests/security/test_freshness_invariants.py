"""Direction A invariant guards — I1 through I5 (ADR-0020).

Written at design lock, before the behaviour they guard existed. Guards whose
subject does not exist yet carry ``xfail(strict=True)``: red today for the
right reason (the module or API is missing), and the milestone that implements
the subject must delete the marker, because a strict xfail that starts passing
errors the suite. Guards over structure that already exists (I3, I4, and the
two-axis separation) are green from day one and must stay green forever.

The invariants, from the Direction A specification:

- I1  No LLM and no network anywhere on the write path; the freshness engine
      is stdlib + provalume only.
- I2  Append-only: a full freshness cycle appends events and mutates nothing.
- I3  Rebuild is byte-identical with freshness events in the journal, and
      rebuild never executes a verification command.
- I4  Agents gain no new write power: no freshness write capability exists on
      the MCP surface.
- I5  Fail open: a failure inside extraction, triggering, or execution leaves
      the record in its prior state and never propagates.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

import provalume
from provalume.mcp.permissions import (
    FORBIDDEN_TOOLS,
    READ_TOOLS,
    WRITE_TOOLS,
    assert_surface_is_safe,
)
from provalume.schemas.events import EVIDENCE_EVENT_TYPES, EventType
from provalume.schemas.freshness import FreshnessState
from provalume.schemas.trust import Source, TrustState

SOURCE_ROOT = Path(provalume.__file__).parent
FRESHNESS_PACKAGE = SOURCE_ROOT / "freshness"

#: The MCP tool names that would constitute freshness write power. Their
#: absence is asserted structurally (they are in FORBIDDEN_TOOLS) and
#: extensionally (no exposed tool set contains them).
FRESHNESS_WRITE_TOOLS = frozenset(
    {
        "reverify",
        "reverify_record",
        "trigger_freshness",
        "set_freshness",
        "mark_stale",
        "mark_current",
        "record_blast_radius",
    }
)

FRESHNESS_EVENT_TYPES = frozenset(
    {
        EventType.BLAST_RADIUS_RECORDED,
        EventType.FRESHNESS_TRIGGERED,
        EventType.RELEVANCE_ASSESSED,
        EventType.REVERIFICATION_EXECUTED,
    }
)


def _freshness_events(pv) -> list:
    """One event of each freshness type, shaped per EVENTS.md."""
    return [
        pv.record_event(
            EventType.BLAST_RADIUS_RECORDED,
            source=Source.KERNEL,
            payload={
                "record_id": "mem_guard",
                "method": "coverage",
                "paths": ["src/pkg/mod.py"],
                "tool": "coverage.py",
                "tool_version": "7.0",
            },
            commit_sha="a" * 40,
        ),
        pv.record_event(
            EventType.FRESHNESS_TRIGGERED,
            source=Source.KERNEL,
            payload={
                "record_id": "mem_guard",
                "trigger_commit": "b" * 40,
                "changed_paths": ["src/pkg/mod.py", "README.md"],
                "intersecting_paths": ["src/pkg/mod.py"],
            },
        ),
        pv.record_event(
            EventType.RELEVANCE_ASSESSED,
            source=Source.KERNEL,
            payload={
                "record_id": "mem_guard",
                "trigger_commit": "b" * 40,
                "verdict": "relevant",
                "differ_version": "1",
                "reason_code": "body_changed",
            },
        ),
        pv.record_event(
            EventType.REVERIFICATION_EXECUTED,
            source=Source.KERNEL,
            payload={
                "record_id": "mem_guard",
                "trigger_commit": "b" * 40,
                "command": "pytest -q tests/",
                "exit_code": 1,
                "duration_ms": 1200,
                "environment_fingerprint": "sha256:" + "c" * 64,
                "outcome": "failed",
            },
        ),
    ]


def _projection_snapshot(pv) -> str:
    records = sorted(
        (m.model_dump_json() for m in pv.memory_records()),
    )
    return "\n".join(records)


# --- I1: the freshness engine is deterministic stdlib + provalume ------------


@pytest.mark.xfail(
    strict=True,
    reason="red until M1: the freshness engine package does not exist yet",
)
def test_freshness_engine_package_exists() -> None:
    """Anti-vacuity for the purity walk below: an empty package would make
    the I1 guard pass by scanning nothing."""
    assert FRESHNESS_PACKAGE.is_dir()
    assert any(FRESHNESS_PACKAGE.glob("*.py"))


def test_freshness_engine_imports_only_stdlib_and_provalume() -> None:
    """I1. No model SDK, no network client, no third-party dependency of any
    kind inside the freshness engine. The repo-wide no-network walk covers
    these modules too; this is the stricter, freshness-specific bound.

    Vacuously green until the package exists; the anti-vacuity guard above is
    what makes that visible rather than silent.
    """
    stdlib = set(sys.stdlib_module_names)
    for path in sorted(FRESHNESS_PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                roots = [node.module.split(".")[0]]
            for root in roots:
                assert root in stdlib or root == "provalume", (
                    f"{path.name} imports {root!r}; the freshness engine may "
                    "import only the standard library and provalume itself (I1)"
                )


# --- I2: append-only across the full cycle -----------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="red until M4: the trigger and re-verification path does not exist yet",
)
def test_full_freshness_cycle_only_appends(pv) -> None:
    """I2. Seed a verified record, run the whole cycle — radius, trigger,
    failed re-run — and prove the journal only grew and no prior event
    changed. The cycle entry points are the planned engine surface; this
    test binds their names.
    """
    from provalume.freshness.executor import reverify_record
    from provalume.freshness.watcher import process_landed_commit

    pv.record_verification(command="pytest -q tests/", passed=True)
    before_events = list(pv.events())
    before_hashes = [e.event_hash for e in before_events]

    process_landed_commit(pv, commit_sha="b" * 40)
    reverify_record(pv, record_id="whatever", trigger_commit="b" * 40)

    after_events = list(pv.events())
    assert len(after_events) >= len(before_events)
    assert [e.event_hash for e in after_events[: len(before_events)]] == before_hashes


# --- I3: rebuild is deterministic and inert ----------------------------------


def test_rebuild_is_byte_identical_with_freshness_events_present(pv) -> None:
    """I3. Freshness events in the journal must not make rebuild
    nondeterministic. Green from M0 onward — this is also the proof that
    declaring the event types broke nothing."""
    pv.record_verification(command="pytest -q tests/", passed=True)
    _freshness_events(pv)
    pv.rebuild()
    first = _projection_snapshot(pv)
    pv.rebuild()
    second = _projection_snapshot(pv)
    assert first == second


def test_rebuild_never_executes_a_command(pv, monkeypatch: pytest.MonkeyPatch) -> None:
    """I3. Re-verification results are journal inputs. A rebuild that shells
    out would be recomputing evidence, which is exactly what append-only
    forbids."""

    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("rebuild attempted to execute a subprocess (I3)")

    pv.record_verification(command="pytest -q tests/", passed=False, excerpt="boom")
    _freshness_events(pv)
    monkeypatch.setattr(subprocess, "run", explode)
    monkeypatch.setattr(subprocess, "Popen", explode)
    pv.rebuild()


# --- I4: no new write power ---------------------------------------------------


def test_freshness_write_capabilities_are_forbidden_on_mcp() -> None:
    """I4. Structural: every freshness write capability is in the forbidden
    set, and no exposed tool set contains one."""
    assert FRESHNESS_WRITE_TOOLS <= FORBIDDEN_TOOLS
    assert not FRESHNESS_WRITE_TOOLS & (READ_TOOLS | WRITE_TOOLS)


def test_write_tool_set_is_exactly_the_pre_freshness_four() -> None:
    """I4. The MCP write surface is pinned. Freshness adds read-only fields
    at most; a fifth write tool appearing here is the invariant breaking."""
    assert {
        "propose",
        "record_observation",
        "report_failure",
        "report_outcome",
    } == WRITE_TOOLS


def test_surface_guard_rejects_a_freshness_tool() -> None:
    """I4, demonstrated: the runtime guard actually bites when a freshness
    write tool is smuggled into a profile."""
    with pytest.raises(AssertionError, match="reverify"):
        assert_surface_is_safe(frozenset({"recall", "reverify"}))


def test_freshness_is_an_axis_not_a_rung() -> None:
    """ADR-0020 §3, structurally: the trust ladder is unchanged, freshness is
    a distinct four-value enum, and no freshness event counts as promotion
    evidence."""
    assert {s.value for s in TrustState} == {
        "quarantined",
        "observed",
        "verified",
        "reviewed",
        "integrated",
        "invalidated",
        "superseded",
        "rejected",
    }
    assert {s.value for s in FreshnessState} == {
        "current",
        "suspect",
        "stale",
        "unverifiable",
    }
    assert not FRESHNESS_EVENT_TYPES & EVIDENCE_EVENT_TYPES


# --- I5: fail open at every stage --------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="red until M1: blast-radius extraction does not exist yet",
)
def test_extraction_failure_leaves_no_trace_and_does_not_raise(
    pv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I5, extraction stage: with every subprocess exploding, extraction
    returns None, appends no blast-radius event, and raises nothing."""
    from provalume.freshness.blast_radius import record_blast_radius

    def explode(*args: object, **kwargs: object) -> None:
        raise OSError("injected: no subprocess available")

    monkeypatch.setattr(subprocess, "run", explode)
    monkeypatch.setattr(subprocess, "Popen", explode)
    before = len(list(pv.events()))
    result = record_blast_radius(pv, record_id="mem_x", command="pytest -q tests/")
    assert result is None
    events = list(pv.events())
    assert len([e for e in events if e.event_type == EventType.BLAST_RADIUS_RECORDED]) == 0
    assert len(events) >= before  # anything it did append is allowed to be a log, never a radius


@pytest.mark.xfail(
    strict=True,
    reason="red until M2: the commit watcher does not exist yet",
)
def test_trigger_failure_leaves_records_untouched(pv, monkeypatch: pytest.MonkeyPatch) -> None:
    """I5, trigger stage: with git unavailable, processing a landed commit
    marks nothing suspect and raises nothing."""
    from provalume.freshness.watcher import process_landed_commit

    def explode(*args: object, **kwargs: object) -> None:
        raise OSError("injected: git unavailable")

    monkeypatch.setattr(subprocess, "run", explode)
    monkeypatch.setattr(subprocess, "Popen", explode)
    pv.record_verification(command="pytest -q tests/", passed=True)
    process_landed_commit(pv, commit_sha="b" * 40)
    triggered = [e for e in pv.events() if e.event_type == EventType.FRESHNESS_TRIGGERED]
    assert triggered == []


@pytest.mark.xfail(
    strict=True,
    reason="red until M4: the re-verification executor does not exist yet",
)
def test_executor_error_is_never_evidence_against_the_record(pv) -> None:
    """I5, execution stage: an engine failure records outcome `errored` at
    most, and an errored outcome produces no freshness transition — the
    engine's own failure is not evidence about the record."""
    from provalume.freshness.executor import reverify_record

    pv.record_verification(command="definitely-not-on-any-allowlist", passed=True)
    result = reverify_record(pv, record_id="mem_x", trigger_commit="b" * 40)
    assert result is None or result.payload.get("outcome") == "errored"
    stale = [
        e
        for e in pv.events()
        if e.event_type == EventType.REVERIFICATION_EXECUTED
        and e.payload.get("outcome") == "failed"
    ]
    assert stale == []
