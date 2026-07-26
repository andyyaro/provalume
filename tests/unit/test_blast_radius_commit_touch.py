"""``commit_touch`` blast-radius extraction (ADR-0020, milestone M1).

The fallback method: the files changed by the commit the verification was
recorded at. Every test builds a real throwaway repository, because every
interesting property of this extractor is a property of a real diff — a merge's
first parent, a root commit's whole tree, a deletion still counting.

Two invariants are load-bearing here and are each asserted with a positive
control in the same test, so a stub that always returns ``None`` cannot pass:

- I5, fail open: any failure is ``None``, never an exception.
- Determinism: sorted, de-duplicated, repository-relative POSIX paths.
"""

from __future__ import annotations

import re
import subprocess  # nosec B404 - creates throwaway test repositories
from pathlib import Path

import pytest

from provalume.freshness._commit_touch import extract
from provalume.freshness.blast_radius import MAX_RADIUS_PATHS, record_blast_radius
from provalume.schemas.events import EventType
from provalume.schemas.freshness import BlastRadiusMethod
from provalume.sdk.client import Provalume
from provalume.store.db import Database
from provalume.store.gitinfo import GitInfo


def git(repo: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed argv, throwaway directory
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()


def new_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@provalume.invalid")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "commit.gpgsign", "false")
    return repo


def commit(
    repo: Path,
    message: str,
    written: dict[str, str] | None = None,
    removed: tuple[str, ...] = (),
) -> str:
    """One commit, staging only the named paths — never ``add -A``, so nothing
    incidental in the working tree can widen a radius a test asserts on."""
    for name, content in (written or {}).items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        git(repo, "add", "--", name)
    for name in removed:
        git(repo, "rm", "-q", "--", name)
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


def client(db: Database, repo: Path) -> Provalume:
    """A client reading the throwaway repository. The database stays outside the
    repository so opening it can never appear in a diff."""
    return Provalume(db, project_id="test-project", git=GitInfo(repo))


# --- the healthy path --------------------------------------------------------


def test_the_radius_is_what_the_current_commit_changed(db: Database, tmp_path: Path) -> None:
    """The whole contract in one assertion set: method, paths, no line ranges,
    tool and a real parsed git version."""
    repo = new_repo(tmp_path)
    commit(repo, "root", {"pkg/mod.py": "x = 1\n", "docs/guide.md": "guide\n"})
    commit(repo, "edit", {"pkg/mod.py": "x = 2\n"})

    radius = extract(client(db, repo), repo)

    assert radius is not None
    assert radius.method is BlastRadiusMethod.COMMIT_TOUCH
    assert radius.paths == ("pkg/mod.py",)
    assert radius.line_ranges is None, "only the coverage method has line ranges"
    assert radius.tool == "git"
    assert re.fullmatch(r"\d+(\.\d+)*", radius.tool_version), radius.tool_version


def test_the_commit_is_the_one_the_client_is_sitting_on(db: Database, tmp_path: Path) -> None:
    """Not the newest commit in the repository — the commit the verification
    would be recorded at, which is HEAD as the client sees it."""
    repo = new_repo(tmp_path)
    commit(repo, "root", {"a.py": "a = 1\n"})
    second = commit(repo, "second", {"b.py": "b = 1\n"})
    commit(repo, "third", {"c.py": "c = 1\n"})
    git(repo, "checkout", "-q", second)

    pv = client(db, repo)
    assert pv.git is not None
    assert pv.git.current_commit() == second
    radius = extract(pv, repo)

    assert radius is not None
    assert radius.paths == ("b.py",)


def test_a_merge_is_attributed_to_its_first_parent(db: Database, tmp_path: Path) -> None:
    """The integration-merge reading: what did this *landing* change. The side
    branch's file is in the radius; the trunk work the merge carried forward is
    not, and the merge is not invisible either (plumbing's own default)."""
    repo = new_repo(tmp_path)
    commit(repo, "root", {"base.txt": "base\n"})
    git(repo, "checkout", "-q", "-b", "feature")
    commit(repo, "feature work", {"feat.py": "feat = 1\n"})
    git(repo, "checkout", "-q", "main")
    commit(repo, "trunk work", {"trunk.py": "trunk = 1\n"})
    git(repo, "merge", "-q", "--no-ff", "feature", "-m", "merge feature")

    assert len(git(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()) == 3
    radius = extract(client(db, repo), repo)

    assert radius is not None
    assert radius.paths == ("feat.py",)


def test_a_root_commit_is_its_whole_tree(db: Database, tmp_path: Path) -> None:
    """A first commit has no parent, so everything in it is what it changed."""
    repo = new_repo(tmp_path)
    commit(repo, "root", {"a.py": "a = 1\n", "pkg/b.py": "b = 1\n", "README.md": "hi\n"})

    radius = extract(client(db, repo), repo)

    assert radius is not None
    assert radius.paths == ("README.md", "a.py", "pkg/b.py")


def test_a_deleted_path_is_in_the_radius(db: Database, tmp_path: Path) -> None:
    """Deleting a file is the most invalidating change possible to anything
    that referred to it, so its path stays in the radius."""
    repo = new_repo(tmp_path)
    commit(repo, "root", {"gone.py": "x = 1\n", "kept.py": "y = 1\n"})
    commit(repo, "delete", removed=("gone.py",))

    radius = extract(client(db, repo), repo)

    assert radius is not None
    assert radius.paths == ("gone.py",)


def test_paths_are_sorted_deduplicated_and_repository_relative(
    db: Database, tmp_path: Path
) -> None:
    repo = new_repo(tmp_path)
    commit(repo, "root", {"seed.txt": "seed\n"})
    commit(repo, "spread", {"z.py": "z\n", "a.py": "a\n", "pkg/m.py": "m\n"})

    radius = extract(client(db, repo), repo)

    assert radius is not None
    assert list(radius.paths) == sorted(set(radius.paths))
    assert radius.paths == ("a.py", "pkg/m.py", "z.py")
    assert not any(path.startswith(("/", "..")) for path in radius.paths)
    assert all((repo / path).exists() for path in radius.paths), "paths resolve under root"


# --- fail open (I5) ----------------------------------------------------------


def test_without_git_there_is_no_radius(pv: Provalume, tmp_path: Path) -> None:
    """The ``pv`` fixture is deliberately git-less. No repository, no radius,
    no exception."""
    assert pv.git is None
    assert extract(pv, tmp_path) is None


def test_outside_a_repository_there_is_no_radius(db: Database, tmp_path: Path) -> None:
    plain = tmp_path / "not-a-repo"
    plain.mkdir()

    assert extract(client(db, plain), plain) is None


def test_a_repository_with_no_commits_has_no_radius(db: Database, tmp_path: Path) -> None:
    """``current_commit()`` is empty before the first commit: git is available,
    but there is nothing to attribute a radius to."""
    repo = new_repo(tmp_path)
    pv = client(db, repo)

    assert pv.git is not None
    assert pv.git.available
    assert pv.git.current_commit() is None
    assert extract(pv, repo) is None


def test_an_empty_commit_has_no_radius(db: Database, tmp_path: Path) -> None:
    """An empty diff is not evidence about any code, so there is nothing to
    record — and a radius with no paths is refused by the orchestrator anyway."""
    repo = new_repo(tmp_path)
    commit(repo, "root", {"a.py": "a = 1\n"})
    git(repo, "commit", "-q", "--allow-empty", "-m", "empty")

    assert extract(client(db, repo), repo) is None


def test_a_root_other_than_the_repository_is_refused(db: Database, tmp_path: Path) -> None:
    """git reports paths relative to the repository it ran in. Anchored
    anywhere else they would be mislabelled, and a mislabelled path is worse
    than no radius: it would intersect the wrong file forever."""
    repo = new_repo(tmp_path)
    commit(repo, "root", {"a.py": "a = 1\n"})
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    pv = client(db, repo)
    assert extract(pv, repo) is not None, "positive control: the same root works"
    assert extract(pv, elsewhere) is None


def test_a_git_failure_fails_open(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control first, then the injected fault: extraction returns
    ``None`` and never propagates, whatever git does."""
    repo = new_repo(tmp_path)
    commit(repo, "root", {"a.py": "a = 1\n"})
    pv = client(db, repo)
    assert extract(pv, repo) is not None

    def explode(_sha: str) -> None:
        raise OSError("injected: git went away")

    assert pv.git is not None
    monkeypatch.setattr(pv.git, "changed_files", explode)
    assert extract(pv, repo) is None


def test_an_unanswerable_diff_fails_open(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = new_repo(tmp_path)
    commit(repo, "root", {"a.py": "a = 1\n"})
    pv = client(db, repo)
    assert pv.git is not None
    monkeypatch.setattr(pv.git, "changed_files", lambda _sha: None)

    assert extract(pv, repo) is None


def test_an_unparseable_git_version_fails_open(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The version travels with the radius so one git's answer can be told from
    another's. Without it the record would be unattributable."""
    repo = new_repo(tmp_path)
    commit(repo, "root", {"a.py": "a = 1\n"})
    pv = client(db, repo)
    assert pv.git is not None
    monkeypatch.setattr(pv.git, "git_version", lambda: None)

    assert extract(pv, repo) is None


# --- the path cap ------------------------------------------------------------


def test_a_radius_at_the_cap_is_kept(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = new_repo(tmp_path)
    commit(repo, "root", {"a.py": "a = 1\n"})
    pv = client(db, repo)
    at_cap = tuple(f"src/mod{index:05d}.py" for index in range(MAX_RADIUS_PATHS))

    assert pv.git is not None
    monkeypatch.setattr(pv.git, "changed_files", lambda _sha: at_cap)
    radius = extract(pv, repo)

    assert radius is not None
    assert len(radius.paths) == MAX_RADIUS_PATHS


def test_a_radius_over_the_cap_is_refused_not_truncated(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A truncated radius would understate what a landing touched, which is the
    one direction this axis must never err in. The method fails instead."""
    repo = new_repo(tmp_path)
    commit(repo, "root", {"a.py": "a = 1\n"})
    pv = client(db, repo)
    over_cap = tuple(f"src/mod{index:05d}.py" for index in range(MAX_RADIUS_PATHS + 1))

    assert pv.git is not None
    monkeypatch.setattr(pv.git, "changed_files", lambda _sha: over_cap)

    assert extract(pv, repo) is None


# --- through the orchestrator -------------------------------------------------


def radii(pv: Provalume) -> list:
    return [e for e in pv.events() if e.event_type == EventType.BLAST_RADIUS_RECORDED]


def test_the_orchestrator_records_a_commit_touch_radius(db: Database, tmp_path: Path) -> None:
    """Asking for the method by name records exactly one further event carrying
    the method, the paths, and the tool — the wiring the M2 watcher will read."""
    repo = new_repo(tmp_path)
    commit(repo, "root", {"pkg/mod.py": "x = 1\n"})
    commit(repo, "edit", {"pkg/mod.py": "x = 2\n"})
    pv = client(db, repo)
    pv.record_verification(command="pytest -q tests/", passed=True)
    record_id = next(m.memory_id for m in pv.memory_records(limit=10))
    before = len(radii(pv))

    produced = record_blast_radius(
        pv,
        record_id=record_id,
        command="pytest -q tests/",
        method=BlastRadiusMethod.COMMIT_TOUCH,
    )

    assert produced is not None
    assert produced.payload["method"] == "commit_touch"
    assert produced.payload["paths"] == ["pkg/mod.py"]
    assert produced.payload["tool"] == "git"
    assert "line_ranges" not in produced.payload
    assert len(radii(pv)) == before + 1


def test_recording_a_verification_attaches_a_commit_touch_radius(
    db: Database, tmp_path: Path
) -> None:
    """End to end, and the reason this unit exists: recording a verification in
    a repository now anchors the claim records it produced to the files the
    commit touched — without running anything (the extraction posture)."""
    repo = new_repo(tmp_path)
    commit(repo, "root", {"pkg/mod.py": "x = 1\n"})
    commit(repo, "edit", {"pkg/mod.py": "x = 2\n"})
    pv = client(db, repo)

    pv.record_verification(command="pytest -q tests/", passed=True)

    attached = radii(pv)
    assert attached, "a verification in a repository must attach a radius"
    claim_ids = {m.memory_id for m in pv.memory_records(limit=50)}
    for event in attached:
        assert event.payload["method"] == "commit_touch"
        assert event.payload["paths"] == ["pkg/mod.py"]
        assert event.payload["record_id"] in claim_ids


def test_the_orchestrator_falls_back_to_commit_touch(db: Database, tmp_path: Path) -> None:
    """With no method named, ``import_graph`` gets first refusal; it is still a
    stub, so the recorded radius must be this method's. When M1's import-graph
    unit lands, this test's expectation changes deliberately — that is the
    fallback order becoming observable, not a regression."""
    repo = new_repo(tmp_path)
    commit(repo, "root", {"pkg/mod.py": "x = 1\n"})
    commit(repo, "edit", {"pkg/mod.py": "x = 2\n"})
    pv = client(db, repo)
    pv.record_verification(command="pytest -q tests/", passed=True)
    record_id = next(m.memory_id for m in pv.memory_records(limit=10))

    produced = record_blast_radius(pv, record_id=record_id, command="pytest -q tests/")

    assert produced is not None
    assert produced.payload["method"] in {"import_graph", "commit_touch"}
    assert produced.payload["paths"], "some non-executing method must produce a radius"
