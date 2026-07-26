"""The `provalume reverify` command, end to end in a real repository."""

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


def _repo_with_record(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("V = 1\n")
    (repo / "check.py").write_text("import mod\nassert mod.V == 1\n")
    _git(tmp_path, "init", "-q", "-b", "main", str(repo))
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "seed")

    from provalume.schemas.memories import MemoryType
    from provalume.sdk.client import Provalume

    db = str(repo / ".provalume" / "db.sqlite")
    pv = Provalume.open(db, project_id="e2e", root=repo)
    pv.record_verification(command=f"{sys.executable} check.py", passed=True)
    record_id = next(
        m.memory_id for m in pv.memory_records(limit=10) if m.memory_type is MemoryType.PROCEDURAL
    )
    pv.close()
    return repo, db, record_id


def test_without_an_allowlist_the_feature_is_off(cli: CliRunner, tmp_path: Path) -> None:
    """Absent or empty allowlist and no --allow: refuse before touching the
    record, exit non-zero, and say why (T27's default-off)."""
    repo, db, record_id = _repo_with_record(tmp_path)
    result = cli("reverify", record_id, "--db", db, "--project", "e2e", cwd=repo, expect=1)
    assert "off" in (result.stdout + result.stderr).lower()


def test_an_allowlist_file_enables_a_passing_rerun(cli: CliRunner, tmp_path: Path) -> None:
    repo, db, record_id = _repo_with_record(tmp_path)
    allowlist = repo / ".provalume" / "reverify-allowlist"
    allowlist.write_text("# evidence commands this repository re-runs\n*check.py\n")
    result = cli(
        "reverify", record_id, "--db", db, "--project", "e2e", "--json", cwd=repo, expect=0
    )
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "passed"
    assert payload["freshness"] == "current"
    assert payload["environment_fingerprint"].startswith("sha256:")


def test_a_failing_rerun_marks_stale_and_exits_two(cli: CliRunner, tmp_path: Path) -> None:
    repo, db, record_id = _repo_with_record(tmp_path)
    (repo / "mod.py").write_text("V = 2222\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "break")
    result = cli(
        "reverify",
        record_id,
        "--db",
        db,
        "--project",
        "e2e",
        "--allow",
        "*check.py",
        "--json",
        cwd=repo,
        expect=2,
    )
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "failed"
    assert payload["freshness"] == "stale"
