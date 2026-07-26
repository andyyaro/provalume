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
    (repo / ".gitignore").write_text(".provalume/\n__pycache__/\n")
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


def test_the_allowlist_binds_to_the_databases_repository_not_the_cwd(
    cli: CliRunner, tmp_path: Path
) -> None:
    """T27's opt-in is per-repository. Repo A holds the db and record and has
    NO allowlist; repo B — where the operator happens to stand — has a
    wide-open one. Running from B against A's db must refuse: B's allowlist
    must not enable execution for A's records, and B's tree must not answer
    for them (M4 review, B2)."""
    repo_a, db, record_id = _repo_with_record(tmp_path)
    repo_b = tmp_path / "elsewhere"
    (repo_b / ".provalume").mkdir(parents=True)
    (repo_b / ".provalume" / "reverify-allowlist").write_text("*\n")
    (repo_b / "check.py").write_text("raise SystemExit(0)\n")
    _git(tmp_path, "init", "-q", "-b", "main", str(repo_b))
    _git(repo_b, "add", "-A")
    _git(repo_b, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "other tree")

    result = cli("reverify", record_id, "--db", db, "--project", "e2e", cwd=repo_b, expect=1)
    assert "off" in (result.stdout + result.stderr).lower(), (
        "the root anchors to the database's repository, where no allowlist exists"
    )

    from provalume.schemas.events import EventType
    from provalume.sdk.client import Provalume

    pv = Provalume.open(db, project_id="e2e", root=repo_a)
    executions = [e for e in pv.events() if e.event_type == EventType.REVERIFICATION_EXECUTED]
    pv.close()
    assert not executions, "nothing may have executed, let alone been journaled"


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
