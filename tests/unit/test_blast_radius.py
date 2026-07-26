"""Radius-on-verification: the orchestrated path, against real repositories.

The M1 acceptance claim lives here: every verification recorded by a
git-backed client yields a blast radius on each claim record it produced,
with method attribution. Git-less clients record none and their records stay
`unverifiable` — the designed degradation (ADR-0020), not a gap.
"""

from __future__ import annotations

import logging
import subprocess  # nosec B404 - creates throwaway test repositories
import sys
from pathlib import Path

import pytest

from provalume.freshness import blast_radius
from provalume.schemas.events import EventType
from provalume.schemas.memories import MemoryType
from provalume.sdk.client import Provalume


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "mod.py").write_text("V = 1\n")
    (repo / "tests" / "test_mod.py").write_text(
        "import mod\n\n\ndef test_v():\n    assert mod.V == 1\n"
    )

    def git(*args: str) -> None:
        subprocess.run(  # noqa: S603 - fixed argv, throwaway directory
            ["git", "-C", str(repo), *args], check=True, capture_output=True
        )

    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "seed")
    return repo


def _radii_by_record(pv: Provalume) -> dict[str, str]:
    return {
        e.payload["record_id"]: e.payload["method"]
        for e in pv.events()
        if e.event_type == EventType.BLAST_RADIUS_RECORDED
    }


def test_every_verification_in_a_repository_gets_a_radius(tmp_path: Path) -> None:
    """The M1 acceptance gate, as a test: a corpus of differently shaped
    verifications — pass, fail, path-naming, path-less — leaves no claim
    record without a radius, each attributed to its method."""
    repo = _repo(tmp_path)
    pv = Provalume.open(repo / ".provalume" / "db.sqlite", project_id="gate", root=repo)
    try:
        pv.record_verification(command=f"{sys.executable} -m pytest tests/", passed=True)
        pv.record_verification(
            command=f"{sys.executable} -m pytest tests/", passed=False, excerpt="E boom"
        )
        pv.record_verification(command="make lint", passed=True)
        pv.record_verification(command="pytest tests/test_mod.py::test_v", passed=True)

        claims = [
            m
            for m in pv.memory_records(include_terminal=True, current_only=False, limit=100)
            if m.memory_type in (MemoryType.PROCEDURAL, MemoryType.GOTCHA)
        ]
        assert claims, "the corpus must have produced claim records"
        radii = _radii_by_record(pv)
        missing = [m.memory_id for m in claims if m.memory_id not in radii]
        assert not missing, f"claim records without a radius: {missing}"
        assert set(radii.values()) <= {"import_graph", "commit_touch"}
        assert "import_graph" in radii.values()
        assert "commit_touch" in radii.values()
    finally:
        pv.close()


def test_a_gitless_client_records_no_radius_and_does_not_fail(pv: Provalume) -> None:
    """The designed degradation: no repository, no radius, no error — the
    record simply stays unverifiable on the freshness axis."""
    event = pv.record_verification(command="pytest -q tests/", passed=True)
    assert event is not None
    radii = [e for e in pv.events() if e.event_type == EventType.BLAST_RADIUS_RECORDED]
    assert radii == []


def test_a_folded_repeat_failure_still_gets_its_radius(tmp_path: Path) -> None:
    """A repeated failure folds into a gotcha that may be arbitrarily old;
    selection by provenance, not by newest-N, is what reaches it
    (M1 review finding 3)."""
    repo = _repo(tmp_path)
    pv = Provalume.open(repo / ".provalume" / "db.sqlite", project_id="fold", root=repo)
    try:
        first = pv.record_verification(
            command=f"{sys.executable} -m pytest tests/", passed=False, excerpt="E boom"
        )
        gotcha = next(
            m
            for m in pv.memories.for_source_event(pv.project_id, first.event_id)
            if m.memory_type is MemoryType.GOTCHA
        )
        # Bury the gotcha far below any newest-N page.
        for index in range(60):
            pv.record_fact(statement=f"filler fact number {index}", subject=f"filler-{index}")

        repeat = pv.record_verification(
            command=f"{sys.executable} -m pytest tests/", passed=False, excerpt="E boom"
        )
        folded = pv.memories.get(gotcha.memory_id)
        assert folded is not None
        assert repeat.event_id in folded.source_event_ids, (
            "precondition: the repeat must fold into the existing gotcha"
        )
        radii = [
            e
            for e in pv.events()
            if e.event_type == EventType.BLAST_RADIUS_RECORDED
            and e.payload["record_id"] == gotcha.memory_id
        ]
        assert len(radii) >= 2, "the folded repeat must re-anchor the old gotcha"
    finally:
        pv.close()


def test_extraction_runs_once_per_verification_not_per_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    pv = Provalume.open(repo / ".provalume" / "db.sqlite", project_id="once", root=repo)
    calls: list[str] = []
    original = blast_radius._extract

    def counting(pv_, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs.get("command", ""))
        return original(pv_, **kwargs)

    monkeypatch.setattr(blast_radius, "_extract", counting)
    try:
        pv.record_verification(command=f"{sys.executable} -m pytest tests/", passed=False)
        assert len(calls) == 1
    finally:
        pv.close()


def test_a_raising_engine_never_breaks_recording(
    pv: Provalume,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The client's belt over the engine (I5): a raise inside radius
    attachment is logged and the verification stands (M1 review finding 17)."""

    def explode(pv_, event):  # type: ignore[no-untyped-def]
        raise RuntimeError("injected engine failure")

    monkeypatch.setattr(blast_radius, "record_radii_for_verification", explode)
    with caplog.at_level(logging.WARNING, logger="provalume.freshness"):
        event = pv.record_verification(command="pytest -q tests/", passed=True)
    assert event is not None
    assert any("radius attachment failed open" in r.message for r in caplog.records)


def test_the_radius_envelope_names_the_commit_extraction_read(tmp_path: Path) -> None:
    """A caller-supplied verification commit_sha must not be claimed as the
    commit the radius was measured at (M1 review finding 19)."""
    repo = _repo(tmp_path)
    pv = Provalume.open(repo / ".provalume" / "db.sqlite", project_id="anchor", root=repo)
    try:
        assert pv.git is not None
        head = pv.git.current_commit()
        pv.record_verification(
            command=f"{sys.executable} -m pytest tests/",
            passed=True,
            commit_sha="f" * 40,
        )
        radii = [e for e in pv.events() if e.event_type == EventType.BLAST_RADIUS_RECORDED]
        assert radii
        assert radii[0].commit_sha == head
        assert radii[0].commit_sha != "f" * 40
    finally:
        pv.close()
