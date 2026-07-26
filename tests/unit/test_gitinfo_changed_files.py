"""``GitInfo.changed_files`` and ``GitInfo.git_version`` against real repositories.

``changed_files`` is the single git-plumbing site behind ``commit_touch`` blast
radii (ADR-0020). Everything interesting about it is a question about *which*
diff a commit means, so every test here builds a throwaway repository and asks
git rather than a mock: an ordinary commit is its parent diff, a merge is its
**first**-parent diff, and a root commit is its whole tree. A stub could agree
with any of those and still be wrong about the other two.
"""

from __future__ import annotations

import re
import subprocess  # nosec B404 - creates throwaway test repositories
from pathlib import Path

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
    """An empty repository with identity configured and signing off.

    ``-b main`` because plain ``git init`` yields ``master`` or ``main``
    depending on the machine's ``init.defaultBranch``.
    """
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
    """One commit, staging only the named paths — never ``add -A``, so a stray
    file in the working tree can never widen a diff a test is asserting on."""
    for name, content in (written or {}).items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        git(repo, "add", "--", name)
    for name in removed:
        git(repo, "rm", "-q", "--", name)
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


# --- which diff a commit means ----------------------------------------------


def test_a_root_commit_is_every_file_in_it(tmp_path: Path) -> None:
    """No parent to diff against, so the commit's whole tree is what it
    changed. The alternative — plumbing's default of printing nothing for a
    parentless commit — would silently drop the radius of a first commit."""
    repo = new_repo(tmp_path)
    root = commit(repo, "root", {"a.txt": "a\n", "pkg/b.py": "b = 1\n"})

    assert GitInfo(repo).changed_files(root) == ("a.txt", "pkg/b.py")


def test_an_ordinary_commit_is_diffed_against_its_parent(tmp_path: Path) -> None:
    repo = new_repo(tmp_path)
    commit(repo, "root", {"a.txt": "a\n", "pkg/b.py": "b = 1\n"})
    second = commit(repo, "edit b", {"pkg/b.py": "b = 2\n"})

    assert GitInfo(repo).changed_files(second) == ("pkg/b.py",)


def test_a_merge_is_diffed_against_its_first_parent(tmp_path: Path) -> None:
    """A real merge, and the semantic decision that matters most.

    For an integration merge, "what did this landing change" is the diff
    against the first parent — the trunk the branch landed on. The side
    branch's file is in the radius; the trunk work the merge merely carried
    forward is not. (Plumbing's own default for a merge is to print nothing at
    all, which would make every landing invisible to freshness.)
    """
    repo = new_repo(tmp_path)
    commit(repo, "root", {"base.txt": "base\n"})
    git(repo, "checkout", "-q", "-b", "feature")
    commit(repo, "feature work", {"feat.py": "feat = 1\n"})
    git(repo, "checkout", "-q", "main")
    trunk = commit(repo, "trunk work", {"trunk.py": "trunk = 1\n"})
    git(repo, "merge", "-q", "--no-ff", "feature", "-m", "merge feature")
    merge = git(repo, "rev-parse", "HEAD")

    parents = git(repo, "rev-list", "--parents", "-n", "1", merge).split()
    assert len(parents) == 3, "the fixture must really be a merge commit"
    assert parents[1] == trunk, "the trunk must be the first parent"

    assert GitInfo(repo).changed_files(merge) == ("feat.py",)


# --- what counts as touched --------------------------------------------------


def test_a_deleted_path_still_counts_as_touched(tmp_path: Path) -> None:
    """Removal is the most invalidating change there is: anything that referred
    to the file is now wrong. Its path stays in the radius."""
    repo = new_repo(tmp_path)
    commit(repo, "root", {"gone.py": "x = 1\n", "kept.py": "y = 1\n"})
    deletion = commit(repo, "delete gone", removed=("gone.py",))

    assert GitInfo(repo).changed_files(deletion) == ("gone.py",)


def test_a_rename_reports_both_names(tmp_path: Path) -> None:
    """Names as git reports them. Plumbing does no rename detection — the
    ``diff.renames`` default is porcelain-only — so a rename is a deletion and
    an addition, and both paths belong in the radius anyway."""
    repo = new_repo(tmp_path)
    commit(repo, "root", {"old.py": "x = 1\n"})
    git(repo, "mv", "old.py", "new.py")
    git(repo, "commit", "-qm", "rename")
    renamed = git(repo, "rev-parse", "HEAD")

    assert GitInfo(repo).changed_files(renamed) == ("new.py", "old.py")


def test_paths_are_sorted_and_deduplicated(tmp_path: Path) -> None:
    """Determinism: the same commit must always yield the same tuple, in the
    same order, whatever order git happened to walk the trees in."""
    repo = new_repo(tmp_path)
    commit(repo, "root", {"seed.txt": "seed\n"})
    spread = commit(
        repo,
        "spread",
        {
            "z.py": "z = 1\n",
            "a.py": "a = 1\n",
            "pkg/m.py": "m = 1\n",
            "pkg/__init__.py": "",
        },
    )

    paths = GitInfo(repo).changed_files(spread)
    assert paths is not None
    assert paths == ("a.py", "pkg/__init__.py", "pkg/m.py", "z.py")
    assert list(paths) == sorted(set(paths))


def test_a_path_with_a_space_is_not_quoted(tmp_path: Path) -> None:
    """NUL-separated output, so git never C-quotes an awkward name. A caller
    comparing ``"docs/release notes.md"`` against a stored
    ``'"docs/release notes.md"'`` would never intersect."""
    repo = new_repo(tmp_path)
    commit(repo, "root", {"seed.txt": "seed\n"})
    awkward = commit(repo, "notes", {"docs/release notes.md": "notes\n"})

    assert GitInfo(repo).changed_files(awkward) == ("docs/release notes.md",)


# --- "nothing changed" is not "cannot answer" --------------------------------


def test_an_empty_commit_reports_no_paths(tmp_path: Path) -> None:
    """``()`` — the question was answered, and the answer is nothing. Callers
    that collapse this into ``None`` lose the ability to say so."""
    repo = new_repo(tmp_path)
    commit(repo, "root", {"a.txt": "a\n"})
    git(repo, "commit", "-q", "--allow-empty", "-m", "empty")
    empty = git(repo, "rev-parse", "HEAD")

    assert GitInfo(repo).changed_files(empty) == ()


def test_an_unknown_commit_is_unanswerable(tmp_path: Path) -> None:
    repo = new_repo(tmp_path)
    commit(repo, "root", {"a.txt": "a\n"})

    assert GitInfo(repo).changed_files("f" * 40) is None
    assert GitInfo(repo).changed_files("") is None


def test_without_a_repository_nothing_is_answerable(tmp_path: Path) -> None:
    """The degradation ADR-0006 requires: no repository is not an exception."""
    info = GitInfo(tmp_path)
    assert not info.available
    assert info.changed_files("a" * 40) is None
    assert info.git_version() is None


# --- the tool version travelling with the evidence ---------------------------


def test_git_version_is_the_numeric_version_only(tmp_path: Path) -> None:
    """``git version 2.39.5 (Apple Git-154)`` must reduce to ``2.39.5``: the
    vendor suffix differs between machines that ship the same git."""
    repo = new_repo(tmp_path)
    version = GitInfo(repo).git_version()

    assert version is not None
    assert re.fullmatch(r"\d+(\.\d+)*", version), version
    assert version in git(repo, "--version")


def test_the_existing_queries_still_work_alongside_the_new_ones(tmp_path: Path) -> None:
    """The addition is additive: the caching queries this module already had
    answer exactly as before on the same instance."""
    repo = new_repo(tmp_path)
    first = commit(repo, "root", {"a.txt": "a\n"})
    second = commit(repo, "next", {"a.txt": "aa\n"})

    info = GitInfo(repo)
    assert info.changed_files(second) == ("a.txt",)
    assert info.current_commit() == second
    assert info.current_branch() == "main"
    assert info.commit_exists(first)
    assert info.is_ancestor(first, second) is True
    assert info.changed_files(first) == ("a.txt",)
