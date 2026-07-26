"""M3 acceptance: the relevance filter, end to end through real landings.

The gate (spec §6, M3): whitespace-, comment-, and docstring-only commits do
not mark records suspect; signature and body changes do; unparseable files
escalate rather than pass. All of it through the real path — a git
repository, a recorded verification with its blast radius, a landed commit,
`process_landed_commit` — because the classifier being right in isolation
proves nothing about the wiring that asks it.
"""

from __future__ import annotations

import subprocess  # nosec B404 - creates throwaway test repositories
import sys
from pathlib import Path

from provalume.freshness.watcher import process_landed_commit
from provalume.schemas.events import EventType
from provalume.schemas.freshness import FreshnessState
from provalume.schemas.memories import MemoryType
from provalume.sdk.client import Provalume

_MOD = 'def greet(name):\n    """Say hello."""\n    # friendly\n    return "hi " + name\n'


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed argv, throwaway directory
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _setup(tmp_path: Path) -> tuple[Path, Provalume, str]:
    """A repo with one verified record whose radius covers mod.py."""
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "mod.py").write_text(_MOD)
    (repo / "tests" / "test_mod.py").write_text(
        'import mod\n\n\ndef test_greet():\n    assert mod.greet("x") == "hi x"\n'
    )
    _git(tmp_path, "init", "-q", "-b", "main", str(repo))
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "seed")
    pv = Provalume.open(repo / ".provalume" / "db.sqlite", project_id="m3", root=repo)
    pv.record_verification(command=f"{sys.executable} -m pytest tests/", passed=True)
    record_id = next(
        m.memory_id for m in pv.memory_records(limit=10) if m.memory_type is MemoryType.PROCEDURAL
    )
    memory = pv.memories.get(record_id)
    assert memory is not None and memory.freshness is FreshnessState.CURRENT, (
        "precondition: the record starts current with a recorded radius"
    )
    return repo, pv, record_id


def _land(repo: Path, filename: str, content: str) -> str:
    (repo / filename).write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", f"edit {filename}")
    return _git(repo, "rev-parse", "HEAD")


def _freshness(pv: Provalume, record_id: str) -> FreshnessState:
    memory = pv.memories.get(record_id)
    assert memory is not None
    return memory.freshness


def _last_reason(pv: Provalume) -> str:
    assessed = [e for e in pv.events() if e.event_type == EventType.RELEVANCE_ASSESSED]
    assert assessed, "the watcher must have assessed the trigger"
    return str(assessed[-1].payload.get("reason_code"))


def test_a_comment_only_landing_leaves_the_record_current(tmp_path: Path) -> None:
    repo, pv, record_id = _setup(tmp_path)
    try:
        sha = _land(repo, "mod.py", _MOD.replace("# friendly", "# cordial"))
        result = process_landed_commit(pv, commit_sha=sha)
        assert result.triggered and result.assessed
        assert _freshness(pv, record_id) is FreshnessState.CURRENT
        assert _last_reason(pv) == "comment_only"
    finally:
        pv.close()


def test_a_whitespace_only_landing_leaves_the_record_current(tmp_path: Path) -> None:
    repo, pv, record_id = _setup(tmp_path)
    try:
        sha = _land(repo, "mod.py", _MOD.replace("\n    return", "\n\n    return"))
        process_landed_commit(pv, commit_sha=sha)
        assert _freshness(pv, record_id) is FreshnessState.CURRENT
        assert _last_reason(pv) == "whitespace_only"
    finally:
        pv.close()


def test_a_docstring_only_landing_leaves_the_record_current(tmp_path: Path) -> None:
    repo, pv, record_id = _setup(tmp_path)
    try:
        sha = _land(repo, "mod.py", _MOD.replace("Say hello.", "Greet someone warmly."))
        process_landed_commit(pv, commit_sha=sha)
        assert _freshness(pv, record_id) is FreshnessState.CURRENT
        assert _last_reason(pv) == "docstring_only"
    finally:
        pv.close()


def test_a_signature_change_marks_the_record_suspect(tmp_path: Path) -> None:
    repo, pv, record_id = _setup(tmp_path)
    try:
        sha = _land(
            repo, "mod.py", _MOD.replace("def greet(name):", "def greet(name, *, loud=False):")
        )
        process_landed_commit(pv, commit_sha=sha)
        assert _freshness(pv, record_id) is FreshnessState.SUSPECT
        assert _last_reason(pv) == "signature_changed"
    finally:
        pv.close()


def test_a_body_change_marks_the_record_suspect(tmp_path: Path) -> None:
    repo, pv, record_id = _setup(tmp_path)
    try:
        sha = _land(repo, "mod.py", _MOD.replace('"hi " + name', '"hello " + name'))
        process_landed_commit(pv, commit_sha=sha)
        assert _freshness(pv, record_id) is FreshnessState.SUSPECT
        assert _last_reason(pv) == "body_changed"
    finally:
        pv.close()


def test_an_unparseable_landing_escalates(tmp_path: Path) -> None:
    """A differ that cannot read a file does not get to call it harmless."""
    repo, pv, record_id = _setup(tmp_path)
    try:
        sha = _land(repo, "mod.py", "def greet(name:\n    syntax error here\n")
        process_landed_commit(pv, commit_sha=sha)
        assert _freshness(pv, record_id) is FreshnessState.SUSPECT
        assert _last_reason(pv) == "unparseable"
    finally:
        pv.close()


def test_an_import_change_marks_the_record_suspect(tmp_path: Path) -> None:
    """The classifier's import lane, pinned through the real path — the M3
    review found it reachable only from unit tests."""
    repo, pv, record_id = _setup(tmp_path)
    try:
        sha = _land(repo, "mod.py", "import os\n" + _MOD)
        process_landed_commit(pv, commit_sha=sha)
        assert _freshness(pv, record_id) is FreshnessState.SUSPECT
        assert _last_reason(pv) == "import_changed"
    finally:
        pv.close()


def test_a_mode_only_landing_escalates(tmp_path: Path) -> None:
    """Git reports the path changed; the text is byte-identical (a chmod, an
    eol conversion the text layer normalised). A change the differ cannot see
    is a change it cannot clear — the most reassuring name in the enum is the
    one thing this must never get."""
    repo, pv, record_id = _setup(tmp_path)
    try:
        _git(repo, "update-index", "--chmod=+x", "mod.py")
        _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "chmod")
        sha = _git(repo, "rev-parse", "HEAD")
        result = process_landed_commit(pv, commit_sha=sha)
        assert result.triggered and result.assessed
        assert _freshness(pv, record_id) is FreshnessState.SUSPECT
        assert _last_reason(pv) == "unparseable"
    finally:
        pv.close()


def test_a_client_rooted_in_a_subdirectory_gets_real_assessments(tmp_path: Path) -> None:
    """A monorepo layout: the client's root is a subdirectory of the git
    toplevel. Radius paths are root-relative; the pre/post fetch must resolve
    them against the root, not the toplevel — the M3 review measured 100% of
    assessments returning `unparseable` before this was fixed."""
    top = tmp_path / "repo"
    pkg = top / "pkg"
    (pkg / "tests").mkdir(parents=True)
    (pkg / "mod.py").write_text(_MOD)
    (pkg / "tests" / "test_mod.py").write_text(
        'import mod\n\n\ndef test_greet():\n    assert mod.greet("x") == "hi x"\n'
    )
    _git(tmp_path, "init", "-q", "-b", "main", str(top))
    _git(top, "add", "-A")
    _git(top, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "seed")
    pv = Provalume.open(top / ".provalume" / "db.sqlite", project_id="m3sub", root=pkg)
    try:
        pv.record_verification(command=f"{sys.executable} -m pytest tests/", passed=True)
        record_id = next(
            m.memory_id
            for m in pv.memory_records(limit=10)
            if m.memory_type is MemoryType.PROCEDURAL
        )
        (pkg / "mod.py").write_text(_MOD.replace("# friendly", "# cordial"))
        _git(top, "add", "-A")
        _git(top, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "comment")
        sha = _git(top, "rev-parse", "HEAD")
        result = process_landed_commit(pv, commit_sha=sha)
        assert result.triggered and result.assessed
        assert _freshness(pv, record_id) is FreshnessState.CURRENT
        assert _last_reason(pv) == "comment_only"
    finally:
        pv.close()


def test_a_comment_reading_command_gets_no_trivia_pass(tmp_path: Path) -> None:
    """A record verified by a linter: `# noqa` IS the outcome, so a
    comment-only landing must escalate rather than discharge (M3 review
    demonstrated a false `current` here end to end)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(_MOD)
    _git(tmp_path, "init", "-q", "-b", "main", str(repo))
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "seed")
    pv = Provalume.open(repo / ".provalume" / "db.sqlite", project_id="m3lint", root=repo)
    try:
        pv.record_verification(command="ruff check mod.py", passed=True)
        record_id = next(
            m.memory_id
            for m in pv.memory_records(limit=10)
            if m.memory_type is MemoryType.PROCEDURAL
        )
        sha = _land(repo, "mod.py", _MOD.replace("# friendly", "# noqa: E501"))
        process_landed_commit(pv, commit_sha=sha)
        assert _freshness(pv, record_id) is FreshnessState.SUSPECT
        assessed = [e for e in pv.events() if e.event_type == EventType.RELEVANCE_ASSESSED]
        assert assessed[-1].payload.get("verdict") == "relevant"
        assert assessed[-1].payload.get("reason_code") == "comment_only", (
            "the reason stays honest; it is the harmlessness that is withdrawn"
        )
    finally:
        pv.close()


def test_trivia_then_real_change_nets_suspect(tmp_path: Path) -> None:
    """Per-trigger bookkeeping through the real path: a comment-only landing
    discharges itself; the body change that follows does not."""
    repo, pv, record_id = _setup(tmp_path)
    try:
        first = _land(repo, "mod.py", _MOD.replace("# friendly", "# cordial"))
        process_landed_commit(pv, commit_sha=first)
        assert _freshness(pv, record_id) is FreshnessState.CURRENT

        second = _land(repo, "mod.py", _MOD.replace('"hi "', '"yo "'))
        process_landed_commit(pv, commit_sha=second)
        assert _freshness(pv, record_id) is FreshnessState.SUSPECT

        pv.rebuild()
        assert _freshness(pv, record_id) is FreshnessState.SUSPECT
    finally:
        pv.close()
