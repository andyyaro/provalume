"""The `provalume freshness` command, end to end in a real repository."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - creates throwaway test repositories
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tests.e2e.conftest import CliRunner


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed argv, throwaway directory
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "mod.py").write_text("V = 1\n")
    (repo / "tests" / "test_mod.py").write_text("import mod\n")
    _git(tmp_path, "init", "-q", "-b", "main", str(repo))
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "seed")
    return repo


def test_a_landed_commit_marks_records_suspect(cli: CliRunner, tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    db = str(repo / ".provalume" / "db.sqlite")

    # Record a verification the normal way — through the SDK, since the CLI
    # deliberately has no verification-recording command for agents to abuse.
    from provalume.sdk.client import Provalume

    pv = Provalume.open(db, project_id="e2e", root=repo)
    pv.record_verification(command=f"{sys.executable} -m pytest tests/", passed=True)
    pv.close()

    (repo / "mod.py").write_text("V = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "land")
    sha = _git(repo, "rev-parse", "HEAD")

    result = cli("freshness", sha, "--db", db, "--project", "e2e", "--json", cwd=repo)
    payload = json.loads(result.stdout)
    assert payload["commit"] == sha
    assert payload["triggered"], "the landing touched mod.py, inside the radius"

    recall = cli("recall", "pytest", "--db", db, "--project", "e2e", "--json", cwd=repo)
    results = json.loads(recall.stdout)
    suspect = [r for r in results if r.get("freshness") == "suspect"]
    assert suspect, f"no suspect record surfaced in recall: {results}"


def test_a_trivia_landing_reports_left_current_not_marked_suspect(
    cli: CliRunner, tmp_path: Path
) -> None:
    """The command triggers and assesses in one pass, and its output must say
    which one decided the record's fate. The M3 review caught it announcing
    'marked suspect' for a landing the assessment had just discharged — the
    exact opposite of what happened."""
    repo = _repo(tmp_path)
    db = str(repo / ".provalume" / "db.sqlite")
    from provalume.sdk.client import Provalume

    pv = Provalume.open(db, project_id="e2e", root=repo)
    pv.record_verification(command=f"{sys.executable} -m pytest tests/", passed=True)
    pv.close()

    (repo / "mod.py").write_text("V = 1  # a comment\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "comment")
    sha = _git(repo, "rev-parse", "HEAD")

    result = cli("freshness", sha, "--db", db, "--project", "e2e", "--json", cwd=repo)
    payload = json.loads(result.stdout)
    assert payload["triggered"], "the landing touched the radius; the trigger is real"
    assert payload["marked_suspect"] == []
    assert payload["left_current"] == payload["triggered"]
    assert payload["assessment_failed"] == 0
    assert [a["verdict"] for a in payload["assessed"]] == ["irrelevant"]
    assert [a["reason_code"] for a in payload["assessed"]] == ["comment_only"]

    text = cli("freshness", sha, "--db", db, "--project", "e2e", cwd=repo)
    combined = text.stdout + text.stderr
    assert "marked suspect" not in combined
    assert "already scanned" in combined, "a re-scan of an assessed trigger is a no-op"

    rescan = cli("freshness", sha, "--db", db, "--project", "e2e", "--json", cwd=repo)
    repayload = json.loads(rescan.stdout)
    assert repayload["triggered"] == [] and repayload["assessed"] == []
    assert repayload["skipped_already_seen"] == 1


def test_left_current_comes_from_the_projection_not_this_scans_verdict(
    cli: CliRunner, tmp_path: Path
) -> None:
    """An irrelevant verdict discharges only its own trigger. A record with
    an OLDER trigger still outstanding must not be reported 'left current'
    just because today's landing was trivia — the M3 fix-verification
    caught the CLI inferring fate from the verdict instead of reading the
    projection."""
    repo = _repo(tmp_path)
    db = str(repo / ".provalume" / "db.sqlite")
    from provalume.sdk.client import Provalume

    pv = Provalume.open(db, project_id="e2e", root=repo)
    pv.record_verification(command=f"{sys.executable} -m pytest tests/", passed=True)
    pv.close()

    (repo / "mod.py").write_text("V = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "body change")
    first = _git(repo, "rev-parse", "HEAD")
    cli("freshness", first, "--db", db, "--project", "e2e", cwd=repo, expect=0)

    (repo / "mod.py").write_text("V = 2  # annotated\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "comment only")
    second = _git(repo, "rev-parse", "HEAD")
    result = cli("freshness", second, "--db", db, "--project", "e2e", "--json", cwd=repo)
    payload = json.loads(result.stdout)
    assert payload["assessed"] and payload["assessed"][0]["verdict"] == "irrelevant", (
        "precondition: this landing really was trivia"
    )
    assert payload["left_current"] == [], "the older trigger is still outstanding"
    assert payload["marked_suspect"] == payload["triggered"]

    text = cli("freshness", second, "--db", db, "--project", "e2e", cwd=repo)
    assert "left current" not in text.stdout + text.stderr


def test_a_bounded_out_record_is_reported_on_every_scan(cli: CliRunner, tmp_path: Path) -> None:
    """Over MAX_ASSESSED_PATHS the trigger books unassessed and the record
    stays suspect. A re-scan must say so — the M3 fix-verification caught
    it printing 'No recorded blast radius intersects … nothing to mark',
    the exact phrase §9d teaches operators to read as a clean result."""
    repo = _repo(tmp_path)
    db = str(repo / ".provalume" / "db.sqlite")
    paths = [f"pkg_{i}.py" for i in range(201)]
    for p in paths:
        (repo / p).write_text("X = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "wide seed")

    from provalume.schemas.events import EventType
    from provalume.schemas.memories import MemoryType
    from provalume.schemas.trust import Source
    from provalume.sdk.client import Provalume

    pv = Provalume.open(db, project_id="e2e", root=repo)
    pv.record_verification(command=f"{sys.executable} -m pytest tests/", passed=True)
    record_id = next(
        m.memory_id for m in pv.memory_records(limit=10) if m.memory_type is MemoryType.PROCEDURAL
    )
    pv.record_event(
        EventType.BLAST_RADIUS_RECORDED,
        source=Source.KERNEL,
        payload={
            "record_id": record_id,
            "method": "import_graph",
            "paths": paths,
            "tool": "ast",
            "tool_version": "1",
        },
    )
    pv.close()

    for p in paths:
        (repo / p).write_text("X = 1  # touched\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "wide landing")
    sha = _git(repo, "rev-parse", "HEAD")

    first = json.loads(
        cli("freshness", sha, "--db", db, "--project", "e2e", "--json", cwd=repo).stdout
    )
    assert first["marked_suspect"] == [record_id]
    assert first["bounded_unassessed"] == 1
    assert first["assessed"] == []

    rescan = json.loads(
        cli("freshness", sha, "--db", db, "--project", "e2e", "--json", cwd=repo).stdout
    )
    assert rescan["bounded_unassessed"] == 1, "a re-scan retries and re-bounds, visibly"

    text = cli("freshness", sha, "--db", db, "--project", "e2e", cwd=repo)
    combined = text.stdout + text.stderr
    assert "No recorded blast radius" not in combined
    assert "unassessed" in combined


def test_an_untouching_commit_marks_nothing(cli: CliRunner, tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    db = str(repo / ".provalume" / "db.sqlite")
    from provalume.sdk.client import Provalume

    pv = Provalume.open(db, project_id="e2e", root=repo)
    pv.record_verification(command=f"{sys.executable} -m pytest tests/", passed=True)
    pv.close()

    (repo / "README.md").write_text("docs only\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "docs")
    sha = _git(repo, "rev-parse", "HEAD")

    result = cli("freshness", sha, "--db", db, "--project", "e2e", "--json", cwd=repo)
    assert json.loads(result.stdout)["triggered"] == []
