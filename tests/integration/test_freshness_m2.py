"""M2 behaviour: freshness derivation, its source gate, and its surfacing.

The derivation table is ADR-0020's; the source gate is the design-lock rule
that only kernel-sourced freshness events derive anything — an imported or
agent-sourced `freshness.triggered` must not be able to relabel a local
record any more than an imported claim can raise its trust (T17, T28).
"""

from __future__ import annotations

from pathlib import Path

import pytest

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

    # The gate matters most on the one event that moves a record TOWARD
    # `current`: an imported or agent-sourced "irrelevant" verdict must not
    # discharge a genuine kernel trigger (M3 review, finding 17).
    for source in (Source.IMPORT, Source.AGENT):
        pv.record_event(
            EventType.RELEVANCE_ASSESSED,
            source=source,
            payload={
                "record_id": record_id,
                "trigger_commit": "b" * 40,
                "verdict": "irrelevant",
                "differ_version": "1",
                "reason_code": "comment_only",
            },
        )
        assert _freshness(pv, record_id) is FreshnessState.SUSPECT, (
            "a non-kernel verdict must not clear suspicion (T17/T28)"
        )
    assert pv.memories.outstanding_triggers(project_id=pv.project_id, record_id=record_id), (
        "the kernel trigger stays outstanding"
    )
    pv.rebuild()
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
    """A suspect record reads `[... · SUSPECT]`; a current one is unmarked;
    `· CURRENT` never renders; and `· UNVERIFIABLE` renders only on
    radius-bearing types — on an episodic record it is the only value the
    type can hold, and a constant marker is noise, not a signal."""
    record_id = _claim(pv)
    digest = pv.recall("pytest", limit=5).digest(char_budget=2000)
    assert "· UNVERIFIABLE" in digest.text, "a radius-bearing record without a radius must say so"

    _radius(pv, record_id)
    digest = pv.recall("pytest", limit=5).digest(char_budget=2000)
    assert "· SUSPECT" not in digest.text
    assert "· CURRENT" not in digest.text, "current is the unmarked state, always"
    assert "· UNVERIFIABLE" not in digest.text, (
        "the episodic record's unverifiable is structural, not a signal"
    )

    _trigger(pv, record_id)
    digest = pv.recall("pytest", limit=5).digest(char_budget=2000)
    assert "· SUSPECT" in digest.text
    assert "· CURRENT" not in digest.text
    item = next(i for i in digest.items if i.memory_id == record_id)
    assert item.freshness == "suspect"


def test_one_verdict_discharges_only_its_own_trigger(pv: Provalume) -> None:
    """Two landings touched the record; ruling ONE of them irrelevant must
    not return the record to current — the other change was never assessed.
    (M2 review finding 2: the false `current` this axis exists to prevent.)"""
    record_id = _claim(pv)
    _radius(pv, record_id)

    def trigger(commit: str) -> None:
        pv.record_event(
            EventType.FRESHNESS_TRIGGERED,
            source=Source.KERNEL,
            payload={
                "record_id": record_id,
                "trigger_commit": commit,
                "changed_paths": ["mod.py"],
                "changed_paths_total": 1,
                "intersecting_paths": ["mod.py"],
            },
        )

    def assess(commit: str, verdict: str) -> None:
        pv.record_event(
            EventType.RELEVANCE_ASSESSED,
            source=Source.KERNEL,
            payload={
                "record_id": record_id,
                "trigger_commit": commit,
                "verdict": verdict,
                "differ_version": "1",
                "reason_code": "comment_only" if verdict == "irrelevant" else "body_changed",
            },
        )

    trigger("1" * 40)
    trigger("2" * 40)
    assert _freshness(pv, record_id) is FreshnessState.SUSPECT

    assess("1" * 40, "irrelevant")
    assert _freshness(pv, record_id) is FreshnessState.SUSPECT, (
        "commit 2 was never assessed; the record must stay suspect"
    )
    assess("2" * 40, "irrelevant")
    assert _freshness(pv, record_id) is FreshnessState.CURRENT

    pv.rebuild()
    assert _freshness(pv, record_id) is FreshnessState.CURRENT


def test_a_second_radius_replaces_the_first(pv: Provalume) -> None:
    """Latest wins, in the intersection table too (M2 review mutation gap)."""
    record_id = _claim(pv)
    _radius(pv, record_id)  # paths=["mod.py"]
    pv.record_event(
        EventType.BLAST_RADIUS_RECORDED,
        source=Source.KERNEL,
        payload={
            "record_id": record_id,
            "method": "import_graph",
            "paths": ["other.py"],
            "tool": "ast",
            "tool_version": "3.12",
        },
    )
    assert pv.memories.records_touching(pv.project_id, ("mod.py",)) == {}
    assert pv.memories.records_touching(pv.project_id, ("other.py",)) == {record_id: ("other.py",)}


def test_terminal_records_are_left_alone(pv: Provalume) -> None:
    """A withdrawn record is neither intersected nor relabelled
    (M2 review finding 8)."""
    record_id = _claim(pv)
    _radius(pv, record_id)
    pv.invalidate(record_id, reason="withdrawn for the test")
    assert pv.memories.records_touching(pv.project_id, ("mod.py",)) == {}, (
        "a terminal record must not be intersected"
    )
    before = _freshness(pv, record_id)
    _trigger(pv, record_id)
    assert _freshness(pv, record_id) is before, "a terminal record must not be relabelled"


def test_recall_and_preflight_carry_freshness(pv: Provalume) -> None:
    pv.record_verification(command="pytest -q tests/", passed=False, excerpt="E boom")
    gotcha = next(m for m in pv.memory_records(limit=10) if m.memory_type is MemoryType.GOTCHA)
    _radius(pv, gotcha.memory_id)
    _trigger(pv, gotcha.memory_id)

    result = next(r for r in pv.recall("boom", limit=5) if r.memory_id == gotcha.memory_id)
    assert result.freshness is FreshnessState.SUSPECT

    match = pv.preflight(command="pytest -q tests/", record=False).matches[0]
    assert match.freshness is FreshnessState.SUSPECT


def test_the_watcher_is_idempotent_and_reports_honestly(tmp_path: Path) -> None:
    """Re-scanning a commit appends nothing new (T29); an unreadable commit
    says so instead of claiming a clean no-op (M2 review findings 3 and 7)."""
    import subprocess  # nosec B404 - throwaway repository
    import sys

    from provalume.freshness.watcher import process_landed_commit

    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "mod.py").write_text("V = 1\n")
    (repo / "tests" / "test_mod.py").write_text("import mod\n")

    def git(*args: str) -> str:
        return subprocess.run(  # noqa: S603 - fixed argv, throwaway directory
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "seed")
    pv = Provalume.open(repo / ".provalume" / "db.sqlite", project_id="idem", root=repo)
    try:
        pv.record_verification(command=f"{sys.executable} -m pytest tests/", passed=True)
        (repo / "mod.py").write_text("V = 2\n")
        git("add", "-A")
        git("-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "land")
        sha = git("rev-parse", "HEAD")

        first = process_landed_commit(pv, commit_sha=sha)
        assert first.commit_readable and first.completed
        assert len(first.triggered) == 1

        second = process_landed_commit(pv, commit_sha=sha)
        assert second.triggered == []
        assert second.skipped == 1
        triggers = [e for e in pv.events() if e.event_type == EventType.FRESHNESS_TRIGGERED]
        assert len(triggers) == 1, "a re-scan must not multiply journal volume"

        unreadable = process_landed_commit(pv, commit_sha="0" * 40)
        assert unreadable.commit_readable is False
        assert unreadable.triggered == []
    finally:
        pv.close()


def test_a_mid_scan_failure_reports_what_it_did(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-open with honest reporting: the events appended before the
    failure are returned, not denied (M2 review finding 4)."""
    import subprocess  # nosec B404 - throwaway repository
    import sys

    import pytest as _pytest  # noqa: F401 - fixture import parity

    from provalume.freshness.watcher import process_landed_commit
    from provalume.sdk.client import Provalume as Client

    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "shared.py").write_text("V = 1\n")
    (repo / "tests" / "test_a.py").write_text("import shared\n")
    (repo / "tests" / "test_b.py").write_text("import shared\n")

    def git(*args: str) -> str:
        return subprocess.run(  # noqa: S603 - fixed argv, throwaway directory
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "seed")
    pv = Client.open(repo / ".provalume" / "db.sqlite", project_id="partial", root=repo)
    try:
        pv.record_verification(command=f"{sys.executable} -m pytest tests/test_a.py", passed=True)
        pv.record_verification(command=f"{sys.executable} -m pytest tests/test_b.py", passed=True)
        (repo / "shared.py").write_text("V = 2\n")
        git("add", "-A")
        git("-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "land")
        sha = git("rev-parse", "HEAD")

        original = type(pv).record_event
        calls = {"triggers": 0}

        def failing_second(self, event_type, *args, **kwargs):  # type: ignore[no-untyped-def]
            # Target the second TRIGGER write specifically: a failed relevance
            # assessment fails open by design and must not trip this test.
            if event_type == EventType.FRESHNESS_TRIGGERED:
                calls["triggers"] += 1
                if calls["triggers"] == 2:
                    raise RuntimeError("injected journal failure")
            return original(self, event_type, *args, **kwargs)

        monkeypatch.setattr(type(pv), "record_event", failing_second)
        result = process_landed_commit(pv, commit_sha=sha)
        assert result.completed is False
        assert len(result.triggered) == 1, "work already done must be reported, not denied"
    finally:
        pv.close()
