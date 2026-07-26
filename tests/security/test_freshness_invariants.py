"""Direction A invariant guards — I1 through I5 (ADR-0020).

Written at design lock, before the behaviour they guard existed. Guards whose
subject does not exist yet carry ``xfail(strict=True)``: red today for the
right reason (the module or API is missing), and the milestone that implements
the subject must delete the marker, because a strict xfail that starts passing
errors the suite. Guards over structure that already exists are green from day
one and must stay green forever.

Every red guard pairs its negative assertion with a positive control in the
same test — the fault-free path must visibly *do* something — so a no-op stub
cannot legally remove a marker. "Fails open" and "never does anything" must
stay distinguishable.

The invariants, from the Direction A specification:

- I1  No LLM and no network anywhere on the write path; the freshness engine
      is stdlib + provalume only.
- I2  Append-only: freshness processing appends events and mutates nothing.
      (The trigger half is guarded here red-until-M2; the execution half is
      asserted inside the I5 executor guard, red-until-M4 — together they
      cover the full invalidation cycle.)
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
from provalume.schemas.freshness import (
    DEFAULT_FRESHNESS,
    IRRELEVANT_REASON_CODES,
    BlastRadiusMethod,
    FreshnessState,
    ReasonCode,
    RelevanceVerdict,
    ReverificationOutcome,
)
from provalume.schemas.trust import Source, TrustState
from provalume.sdk.client import Provalume

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


def _freshness_events(pv: Provalume) -> list:
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
                "timeout_ms": 60_000,
                "environment_fingerprint": "sha256:" + "c" * 64,
                "outcome": "failed",
            },
        ),
    ]


def _projection_snapshot(pv: Provalume) -> str:
    """Every memory row (terminal and non-current included) plus every
    transition row. Narrower scopes have hidden nondeterminism before.

    ``transition_id`` and transition ``recorded_at`` are excluded: both are
    regenerated on every rebuild (a fresh ULID and a fresh wall-clock stamp —
    pre-existing behaviour, demonstrated on records no freshness event ever
    touched, and now recorded in LIMITATIONS). Every semantic transition
    field — states, rule, evidence, actor, note — is pinned.
    """
    unstable = {"transition_id", "recorded_at"}
    parts: list[str] = []
    records = sorted(
        pv.memory_records(include_terminal=True, current_only=False, limit=1000),
        key=lambda m: m.memory_id,
    )
    for memory in records:
        parts.append(memory.model_dump_json())
        for transition in pv.memories.transitions_for(memory.memory_id, limit=100):
            parts.append(repr(sorted(i for i in transition.items() if i[0] not in unstable)))
    return "\n".join(parts)


def _trust_snapshot(pv: Provalume) -> list[tuple[str, str, int]]:
    return sorted(
        (
            m.memory_id,
            str(m.trust_state),
            len(pv.memories.transitions_for(m.memory_id, limit=100)),
        )
        for m in pv.memory_records(include_terminal=True, current_only=False, limit=1000)
    )


def _land_commit(repo: Path, filename: str, content: str) -> str:
    """A real landed commit in a throwaway repository."""
    (repo / filename).write_text(content)
    subprocess.run(  # noqa: S603 - fixed argv, throwaway directory
        ["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True
    )
    subprocess.run(  # noqa: S603 - fixed argv, throwaway directory
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.email=guard@test",
            "-c",
            "user.name=guard",
            "commit",
            "-qm",
            f"touch {filename}",
        ],
        check=True,
        capture_output=True,
    )
    out = subprocess.run(  # noqa: S603 - fixed argv
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


# --- I1: the freshness engine is deterministic stdlib + provalume ------------


@pytest.mark.xfail(
    strict=True,
    reason="red until M1: the freshness engine package does not exist yet",
)
def test_freshness_engine_is_stdlib_and_provalume_only() -> None:
    """I1. No model SDK, no network client, no third-party import of any kind
    inside the freshness engine. The non-empty assertion is part of the same
    test so the walk can never pass by scanning nothing."""
    assert FRESHNESS_PACKAGE.is_dir(), "src/provalume/freshness/ does not exist"
    files = sorted(FRESHNESS_PACKAGE.rglob("*.py"))
    assert files, "the freshness package contains no modules"
    stdlib = set(sys.stdlib_module_names)
    for path in files:
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


# --- Axis separation: freshness events move no trust, ever -------------------


def test_freshness_events_move_no_trust_state(pv: Provalume) -> None:
    """ADR-0020's central property, asserted behaviourally: recording every
    freshness event type leaves every trust state and every transition row
    exactly as it was — live and after a rebuild. Green from M0; if a future
    projection handler routes a freshness event anywhere near promotion,
    this is the test that goes red."""
    pv.record_verification(command="pytest -q tests/", passed=True)
    pv.record_verification(command="pytest -q tests/", passed=False, excerpt="boom")
    before = _trust_snapshot(pv)
    _freshness_events(pv)
    assert _trust_snapshot(pv) == before
    pv.rebuild()
    assert _trust_snapshot(pv) == before


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


def test_freshness_vocabulary_is_pinned() -> None:
    """The closed sets the whole design leans on, pinned literally. The one
    that matters most: `unparseable` must never become an irrelevant reason —
    a differ that cannot read a file does not get to call the change harmless
    (spec §5.3: when uncertain, escalate)."""
    assert {m.value for m in BlastRadiusMethod} == {
        "coverage",
        "import_graph",
        "commit_touch",
    }
    assert {v.value for v in RelevanceVerdict} == {"relevant", "irrelevant"}
    assert {r.value for r in ReasonCode} == {
        "whitespace_only",
        "comment_only",
        "docstring_only",
        "signature_changed",
        "body_changed",
        "import_changed",
        "unparseable",
    }
    assert {o.value for o in ReverificationOutcome} == {"passed", "failed", "errored"}
    assert {
        ReasonCode.WHITESPACE_ONLY,
        ReasonCode.COMMENT_ONLY,
        ReasonCode.DOCSTRING_ONLY,
    } == IRRELEVANT_REASON_CODES
    assert ReasonCode.UNPARSEABLE not in IRRELEVANT_REASON_CODES
    assert DEFAULT_FRESHNESS is FreshnessState.UNVERIFIABLE


# --- I2: append-only ----------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="red until M2: the commit watcher does not exist yet",
)
def test_trigger_cycle_only_appends(tmp_path: Path) -> None:
    """I2, trigger half. Positive control: a landed commit that touches a
    recorded blast radius MUST append a `freshness.triggered` event naming
    the intersecting path. Append-only: every pre-existing event hash is
    unchanged and the memory rows were not rewritten in place."""
    from provalume.freshness.watcher import process_landed_commit

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(  # noqa: S603 - fixed argv, throwaway directory
        ["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True
    )
    _land_commit(repo, "mod.py", "def f():\n    return 1\n")

    pv = Provalume.open(repo / ".provalume" / "guard.db", project_id="guard", root=repo)
    try:
        pv.record_verification(command="pytest -q tests/", passed=True)
        pv.record_event(
            EventType.BLAST_RADIUS_RECORDED,
            source=Source.KERNEL,
            payload={
                "record_id": next(m.memory_id for m in pv.memory_records(limit=10)),
                "method": "import_graph",
                "paths": ["mod.py"],
                "tool": "ast",
                "tool_version": "1",
            },
        )
        before_hashes = [e.event_hash for e in pv.events()]

        sha = _land_commit(repo, "mod.py", "def f():\n    return 2\n")
        process_landed_commit(pv, commit_sha=sha)

        events = list(pv.events())
        assert [e.event_hash for e in events[: len(before_hashes)]] == before_hashes
        triggered = [e for e in events if e.event_type == EventType.FRESHNESS_TRIGGERED]
        assert triggered, "a touching landed commit must append a trigger event"
        assert "mod.py" in triggered[0].payload.get("intersecting_paths", [])
    finally:
        pv.close()


# --- I3: rebuild is deterministic and inert ----------------------------------


def test_rebuild_is_byte_identical_with_freshness_events_present(pv: Provalume) -> None:
    """I3. Freshness events in the journal must not make rebuild
    nondeterministic. Green from M0 onward — this is also the proof that
    declaring the event types broke nothing."""
    pv.record_verification(command="pytest -q tests/", passed=True)
    pv.record_verification(command="pytest -q tests/", passed=False, excerpt="boom")
    _freshness_events(pv)
    pv.rebuild()
    first = _projection_snapshot(pv)
    pv.rebuild()
    second = _projection_snapshot(pv)
    assert first == second


def test_rebuild_never_executes_a_command(pv: Provalume, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_mcp_tool_sets_are_pinned_literally() -> None:
    """I4. Both halves of the surface, pinned by value rather than derived
    from each other. A fifth write tool is the invariant breaking; a new
    read tool is a deliberate act that must edit this test."""
    assert {
        "propose",
        "record_observation",
        "report_failure",
        "report_outcome",
    } == WRITE_TOOLS
    assert {
        "recall",
        "explain",
        "query_failures",
        "query_decisions",
        "query_procedures",
        "query_facts",
        "query_provenance",
        "preflight",
    } == READ_TOOLS


def test_surface_guard_rejects_a_freshness_tool() -> None:
    """I4, demonstrated: the runtime guard actually bites when a freshness
    write tool is smuggled into a profile."""
    with pytest.raises(AssertionError, match="reverify"):
        assert_surface_is_safe(frozenset({"recall", "reverify"}))


# --- I5: fail open at every stage --------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="red until M1: blast-radius extraction does not exist yet",
)
def test_extraction_fails_open(pv: Provalume, monkeypatch: pytest.MonkeyPatch) -> None:
    """I5, extraction stage. Positive control first: on a healthy path the
    extractor MUST append a `blast_radius.recorded` event. Then with every
    subprocess exploding and an unreadable root, it returns None, appends no
    radius, and raises nothing."""
    from provalume.freshness.blast_radius import record_blast_radius

    pv.record_verification(command="pytest -q tests/", passed=True)
    record_id = next(m.memory_id for m in pv.memory_records(limit=10))

    produced = record_blast_radius(
        pv, record_id=record_id, command=f"{sys.executable} -m pytest tests/"
    )
    assert produced is not None
    radii = [e for e in pv.events() if e.event_type == EventType.BLAST_RADIUS_RECORDED]
    assert len(radii) == 1, "healthy extraction must record exactly one radius"

    def explode(*args: object, **kwargs: object) -> None:
        raise OSError("injected: no subprocess available")

    monkeypatch.setattr(subprocess, "run", explode)
    monkeypatch.setattr(subprocess, "Popen", explode)
    monkeypatch.setattr(Path, "read_text", explode)
    result = record_blast_radius(
        pv, record_id=record_id, command=f"{sys.executable} -m pytest tests/"
    )
    assert result is None
    radii = [e for e in pv.events() if e.event_type == EventType.BLAST_RADIUS_RECORDED]
    assert len(radii) == 1, "a failed extraction must not append a radius event"


@pytest.mark.xfail(
    strict=True,
    reason="red until M2: the commit watcher does not exist yet",
)
def test_trigger_fails_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """I5, trigger stage. Positive control: fault-free processing of a
    touching commit appends a trigger. Then with git unavailable, processing
    appends nothing, marks nothing, and raises nothing."""
    from provalume.freshness.watcher import process_landed_commit

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(  # noqa: S603 - fixed argv, throwaway directory
        ["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True
    )
    _land_commit(repo, "mod.py", "x = 1\n")
    pv = Provalume.open(repo / ".provalume" / "guard.db", project_id="guard", root=repo)
    try:
        pv.record_verification(command="pytest -q tests/", passed=True)
        pv.record_event(
            EventType.BLAST_RADIUS_RECORDED,
            source=Source.KERNEL,
            payload={
                "record_id": next(m.memory_id for m in pv.memory_records(limit=10)),
                "method": "import_graph",
                "paths": ["mod.py"],
                "tool": "ast",
                "tool_version": "1",
            },
        )
        sha = _land_commit(repo, "mod.py", "x = 2\n")
        process_landed_commit(pv, commit_sha=sha)
        healthy = [e for e in pv.events() if e.event_type == EventType.FRESHNESS_TRIGGERED]
        assert healthy, "positive control: the healthy path must trigger"

        def explode(*args: object, **kwargs: object) -> None:
            raise OSError("injected: git unavailable")

        monkeypatch.setattr(subprocess, "run", explode)
        monkeypatch.setattr(subprocess, "Popen", explode)
        sha2 = "d" * 40
        process_landed_commit(pv, commit_sha=sha2)
        after = [e for e in pv.events() if e.event_type == EventType.FRESHNESS_TRIGGERED]
        assert after == healthy, "a failed trigger pass must append nothing"
    finally:
        pv.close()


@pytest.mark.xfail(
    strict=True,
    reason="red until M4: the re-verification executor does not exist yet",
)
def test_executor_fails_open_and_only_appends(
    pv: Provalume, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I5, execution stage — and the execution half of I2. Positive control:
    with the allowlist permitting it, a genuinely failing command appends a
    `reverification.executed` event with outcome `failed`, append-only over
    the prior journal. Then with subprocess exploding, the engine's own
    failure records `errored` at most and never a `failed` outcome — the
    engine's failure is not evidence against the record."""
    from provalume.freshness.executor import reverify_record

    pv.record_verification(command=f"{sys.executable} -c 'raise SystemExit(1)'", passed=True)
    record_id = next(m.memory_id for m in pv.memory_records(limit=10))
    before_hashes = [e.event_hash for e in pv.events()]

    executed = reverify_record(
        pv,
        record_id=record_id,
        trigger_commit="b" * 40,
        allowlist=("*",),
        timeout_s=30.0,
    )
    assert executed is not None
    assert executed.payload.get("outcome") == "failed"
    events = list(pv.events())
    assert [e.event_hash for e in events[: len(before_hashes)]] == before_hashes

    def explode(*args: object, **kwargs: object) -> None:
        raise OSError("injected: execution environment gone")

    monkeypatch.setattr(subprocess, "run", explode)
    monkeypatch.setattr(subprocess, "Popen", explode)
    errored = reverify_record(
        pv,
        record_id=record_id,
        trigger_commit="b" * 40,
        allowlist=("*",),
        timeout_s=30.0,
    )
    assert errored is None or errored.payload.get("outcome") == "errored"
    failed_outcomes = [
        e
        for e in pv.events()
        if e.event_type == EventType.REVERIFICATION_EXECUTED
        and e.payload.get("outcome") == "failed"
    ]
    assert len(failed_outcomes) == 1, (
        "the injected engine failure must never be recorded as the record failing"
    )
