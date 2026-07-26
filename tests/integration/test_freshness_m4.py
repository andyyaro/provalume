"""M4 acceptance: gated re-execution closes the loop, end to end.

The gate (spec §6, M4): with an allowlist configured, a breaking change
flips a verified record to `stale`; a reverting commit restores it to
`current`; the default configuration leaves execution off. All through the
real path — a git repository, a recorded verification with its extracted
radius, landed commits, the watcher, and the executor actually running the
record's own command.
"""

from __future__ import annotations

import subprocess  # nosec B404 - creates throwaway test repositories
import sys
from pathlib import Path

from provalume.freshness.executor import reverify_record
from provalume.freshness.watcher import process_landed_commit
from provalume.schemas.events import EventType
from provalume.schemas.freshness import FreshnessState
from provalume.schemas.memories import MemoryType
from provalume.sdk.client import Provalume

_CHECK = "import mod\nassert mod.V == 1\n"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed argv, throwaway directory
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _land(repo: Path, filename: str, content: str) -> str:
    (repo / filename).write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", f"edit {filename}")
    return _git(repo, "rev-parse", "HEAD")


def _setup(tmp_path: Path) -> tuple[Path, Provalume, str]:
    """A repo whose verified record runs `python check.py`, importing mod."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("V = 1\n")
    (repo / "check.py").write_text(_CHECK)
    _git(tmp_path, "init", "-q", "-b", "main", str(repo))
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "seed")
    pv = Provalume.open(repo / ".provalume" / "db.sqlite", project_id="m4", root=repo)
    pv.record_verification(command=f"{sys.executable} check.py", passed=True)
    record_id = next(
        m.memory_id for m in pv.memory_records(limit=10) if m.memory_type is MemoryType.PROCEDURAL
    )
    memory = pv.memories.get(record_id)
    assert memory is not None and memory.freshness is FreshnessState.CURRENT, (
        "precondition: verified, radius recorded, current"
    )
    return repo, pv, record_id


def _freshness(pv: Provalume, record_id: str) -> FreshnessState:
    memory = pv.memories.get(record_id)
    assert memory is not None
    return memory.freshness


def test_break_then_revert_walks_stale_then_current(tmp_path: Path) -> None:
    """The whole M4 story: suspect → failed re-run → stale; revert →
    suspect → passing re-run → current, outstanding triggers cleared —
    live and identically after a rebuild.

    The breaking content differs in SIZE from the original, deliberately:
    the re-run executes in the real working tree with real caches, and
    CPython validates ``__pycache__`` by (mtime seconds, size) — a
    same-second, same-size revert would re-serve the stale bytecode and the
    passing re-run would fail for the cache's reasons, not the code's."""
    repo, pv, record_id = _setup(tmp_path)
    try:
        breaking = _land(repo, "mod.py", "V = 2222\n")
        process_landed_commit(pv, commit_sha=breaking)
        assert _freshness(pv, record_id) is FreshnessState.SUSPECT

        failed = reverify_record(
            pv,
            record_id=record_id,
            trigger_commit=breaking,
            allowlist=(f"{sys.executable} check.py",),
            timeout_s=60.0,
            root=repo,
        )
        assert failed is not None and failed.payload["outcome"] == "failed"
        assert _freshness(pv, record_id) is FreshnessState.STALE

        reverting = _land(repo, "mod.py", "V = 1\n")
        process_landed_commit(pv, commit_sha=reverting)
        assert _freshness(pv, record_id) is FreshnessState.STALE, (
            "stale dominates: a further touch cannot make a failed re-run less failed"
        )

        passed = reverify_record(
            pv,
            record_id=record_id,
            trigger_commit=reverting,
            allowlist=(f"{sys.executable} check.py",),
            timeout_s=60.0,
            root=repo,
        )
        assert passed is not None and passed.payload["outcome"] == "passed"
        assert _freshness(pv, record_id) is FreshnessState.CURRENT
        assert not pv.memories.outstanding_triggers(
            project_id=pv.project_id, record_id=record_id
        ), "a passing re-run answers every outstanding trigger"

        pv.rebuild()
        assert _freshness(pv, record_id) is FreshnessState.CURRENT
    finally:
        pv.close()


def test_the_default_configuration_leaves_execution_off(tmp_path: Path) -> None:
    """No allowlist, no execution, no event, no transition — enabling this
    feature is an explicit operator act (T27)."""
    repo, pv, record_id = _setup(tmp_path)
    try:
        breaking = _land(repo, "mod.py", "V = 2\n")
        process_landed_commit(pv, commit_sha=breaking)
        assert _freshness(pv, record_id) is FreshnessState.SUSPECT

        refused = reverify_record(
            pv,
            record_id=record_id,
            trigger_commit=breaking,
            allowlist=(),
            timeout_s=60.0,
            root=repo,
        )
        assert refused is None
        executions = [e for e in pv.events() if e.event_type == EventType.REVERIFICATION_EXECUTED]
        assert not executions
        assert _freshness(pv, record_id) is FreshnessState.SUSPECT
    finally:
        pv.close()


def test_a_reverification_journal_rebuilds_without_executing(tmp_path: Path) -> None:
    """I3: the re-run's result is a journal input. Rebuilding a journal that
    contains reverification events spawns nothing — the recorded outcome is
    replayed, never recomputed."""
    repo, pv, record_id = _setup(tmp_path)
    try:
        breaking = _land(repo, "mod.py", "V = 2\n")
        process_landed_commit(pv, commit_sha=breaking)
        reverify_record(
            pv,
            record_id=record_id,
            trigger_commit=breaking,
            allowlist=("*",),
            timeout_s=60.0,
            root=repo,
        )
        assert _freshness(pv, record_id) is FreshnessState.STALE

        original_run = subprocess.run
        original_popen = subprocess.Popen

        def guarded_run(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
            head = Path(argv[0]).name if isinstance(argv, (list, tuple)) else str(argv)
            assert head == "git", f"rebuild spawned {head!r} (only git is allowed)"
            return original_run(argv, *args, **kwargs)

        def guarded_popen(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
            head = Path(argv[0]).name if isinstance(argv, (list, tuple)) else str(argv)
            assert head == "git", f"rebuild spawned {head!r} (only git is allowed)"
            return original_popen(argv, *args, **kwargs)

        subprocess.run = guarded_run  # type: ignore[assignment]
        subprocess.Popen = guarded_popen  # type: ignore[assignment]
        try:
            pv.rebuild()
        finally:
            subprocess.run = original_run  # type: ignore[assignment]
            subprocess.Popen = original_popen  # type: ignore[assignment]
        assert _freshness(pv, record_id) is FreshnessState.STALE
    finally:
        pv.close()
