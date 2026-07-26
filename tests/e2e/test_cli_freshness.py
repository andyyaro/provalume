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
