"""Branch and commit validity against a real Git repository (ADR-0006).

The rule under test: a query as of commit X must not present a fact introduced
after X as current truth — and where ancestry cannot be established, the answer
is ``uncertain`` rather than a guess.
"""

from __future__ import annotations

import subprocess  # nosec B404 - operates on throwaway test repositories
from pathlib import Path

from provalume.schemas.scope import Applicability
from provalume.sdk.client import Provalume
from provalume.store.gitinfo import GitInfo, applicability_at


def git(repo: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed argv, throwaway repository
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout.strip()


# --- GitInfo basics --------------------------------------------------------


def test_detects_a_repository(git_repo: Path) -> None:
    info = GitInfo(git_repo)
    assert info.available
    assert info.current_commit()
    assert info.current_branch() in {"main", "master"}


def test_degrades_gracefully_without_a_repository(tmp_path: Path) -> None:
    """A real capability is lost, not merely degraded — documented, not hidden."""
    info = GitInfo(tmp_path)
    assert not info.available
    assert info.current_commit() is None
    assert info.current_branch() is None
    assert info.is_ancestor("a" * 40, "b" * 40) is None


def test_repository_id_prefers_the_remote(git_repo: Path) -> None:
    assert GitInfo(git_repo).repository_id() == git_repo.name
    git(git_repo, "remote", "add", "origin", "https://example.invalid/repo.git")
    assert GitInfo(git_repo).repository_id() == "https://example.invalid/repo.git"


def test_commit_existence(git_repo: Path, git_commits: list[str]) -> None:
    info = GitInfo(git_repo)
    assert info.commit_exists(git_commits[0])
    assert not info.commit_exists("f" * 40)


# --- Ancestry is tri-state -------------------------------------------------


def test_ancestor_returns_true_for_an_earlier_commit(
    git_repo: Path, git_commits: list[str]
) -> None:
    info = GitInfo(git_repo)
    assert info.is_ancestor(git_commits[0], git_commits[2]) is True


def test_ancestor_returns_false_for_a_later_commit(
    git_repo: Path, git_commits: list[str]
) -> None:
    info = GitInfo(git_repo)
    assert info.is_ancestor(git_commits[2], git_commits[0]) is False


def test_ancestor_returns_none_for_an_unknown_commit(
    git_repo: Path, git_commits: list[str]
) -> None:
    """The tri-state is the point: collapsing None into False would turn
    "I cannot tell" into "definitely not"."""
    info = GitInfo(git_repo)
    assert info.is_ancestor("f" * 40, git_commits[0]) is None


def test_a_commit_is_its_own_ancestor(git_repo: Path, git_commits: list[str]) -> None:
    assert GitInfo(git_repo).is_ancestor(git_commits[0], git_commits[0]) is True


# --- Applicability ---------------------------------------------------------


def test_record_without_a_commit_is_judged_by_scope(git_repo: Path) -> None:
    applicability, reason = applicability_at(
        record_commit=None,
        query_commit="a" * 40,
        git=GitInfo(git_repo),
        scope_applicability=Applicability.CURRENT,
    )
    assert applicability is Applicability.CURRENT
    assert "not anchored" in reason


def test_ancestor_record_is_current(git_repo: Path, git_commits: list[str]) -> None:
    applicability, reason = applicability_at(
        record_commit=git_commits[0],
        query_commit=git_commits[2],
        git=GitInfo(git_repo),
        scope_applicability=Applicability.CURRENT,
    )
    assert applicability is Applicability.CURRENT
    assert "ancestor" in reason


def test_non_ancestor_record_is_historical(
    git_repo: Path, git_commits: list[str]
) -> None:
    """The core rule: a fact introduced later is not current truth at an
    earlier commit."""
    applicability, _ = applicability_at(
        record_commit=git_commits[2],
        query_commit=git_commits[0],
        git=GitInfo(git_repo),
        scope_applicability=Applicability.CURRENT,
    )
    assert applicability is Applicability.HISTORICAL


def test_unknown_commit_is_uncertain_not_assumed_valid(
    git_repo: Path, git_commits: list[str]
) -> None:
    applicability, reason = applicability_at(
        record_commit="f" * 40,
        query_commit=git_commits[0],
        git=GitInfo(git_repo),
        scope_applicability=Applicability.CURRENT,
    )
    assert applicability is Applicability.UNCERTAIN
    assert "rebased" in reason or "could not be determined" in reason


def test_no_repository_yields_uncertain(tmp_path: Path) -> None:
    applicability, reason = applicability_at(
        record_commit="a" * 40,
        query_commit="b" * 40,
        git=GitInfo(tmp_path),
        scope_applicability=Applicability.CURRENT,
    )
    assert applicability is Applicability.UNCERTAIN
    assert "no repository" in reason


def test_query_without_a_commit_is_uncertain(git_repo: Path) -> None:
    applicability, _ = applicability_at(
        record_commit="a" * 40,
        query_commit=None,
        git=GitInfo(git_repo),
        scope_applicability=Applicability.CURRENT,
    )
    assert applicability is Applicability.UNCERTAIN


# --- Topologies that are honestly hard --------------------------------------


def test_rebase_degrades_to_uncertain_rather_than_guessing(
    git_repo: Path, git_commits: list[str]
) -> None:
    """After a rebase the original SHAs are unreachable. Provalume detects that
    it cannot tell and says so, which beats a confident wrong answer."""
    git(git_repo, "checkout", "-q", "-b", "feature", git_commits[0])
    (git_repo / "feature.txt").write_text("feature\n")
    git(git_repo, "add", "-A")
    git(git_repo, "commit", "-qm", "feature work")
    original = git(git_repo, "rev-parse", "HEAD")

    git(git_repo, "rebase", "-q", git_commits[2])
    rebased = git(git_repo, "rev-parse", "HEAD")
    assert original != rebased, "the rebase did not rewrite the commit"

    info = GitInfo(git_repo)
    # The original SHA is unreachable from the rebased head; git may still have
    # the object, in which case the honest answer is "not an ancestor".
    verdict = info.is_ancestor(original, rebased)
    assert verdict in {False, None}, "a rewritten commit must not read as an ancestor"


def test_cherry_pick_produces_a_different_sha(
    git_repo: Path, git_commits: list[str]
) -> None:
    """Same change, different identity — so ancestry legitimately fails and the
    result is labelled rather than asserted."""
    git(git_repo, "checkout", "-q", "-b", "side", git_commits[0])
    (git_repo / "side.txt").write_text("side\n")
    git(git_repo, "add", "-A")
    git(git_repo, "commit", "-qm", "side work")
    source = git(git_repo, "rev-parse", "HEAD")

    git(git_repo, "checkout", "-q", "main")
    git(git_repo, "cherry-pick", source)
    picked = git(git_repo, "rev-parse", "HEAD")

    assert source != picked
    assert GitInfo(git_repo).is_ancestor(source, picked) is False


def test_merge_commit_reaches_both_parents(
    git_repo: Path, git_commits: list[str]
) -> None:
    git(git_repo, "checkout", "-q", "-b", "topic", git_commits[0])
    (git_repo / "topic.txt").write_text("topic\n")
    git(git_repo, "add", "-A")
    git(git_repo, "commit", "-qm", "topic work")
    topic = git(git_repo, "rev-parse", "HEAD")

    git(git_repo, "checkout", "-q", "main")
    git(git_repo, "merge", "--no-ff", "-q", "-m", "merge topic", "topic")
    merged = git(git_repo, "rev-parse", "HEAD")

    info = GitInfo(git_repo)
    assert info.is_ancestor(topic, merged) is True
    assert info.is_ancestor(git_commits[2], merged) is True


def test_deleted_branch_leaves_records_retrievable(git_repo: Path) -> None:
    pv = Provalume.open(project_id="p", root=git_repo)
    pv.record_fact(statement="A fact from a doomed branch.", subject="doomed",
                   branch="gone")
    git(git_repo, "checkout", "-q", "-b", "gone")
    git(git_repo, "checkout", "-q", "main")
    git(git_repo, "branch", "-D", "gone")

    records = pv.memory_records(include_terminal=True, current_only=False, limit=10)
    assert any("doomed branch" in m.text for m in records), (
        "deleting a branch must not delete its recorded history"
    )
    pv.close()


# --- Through the SDK -------------------------------------------------------


def test_sdk_fills_branch_and_commit_from_the_repository(git_repo: Path) -> None:
    pv = Provalume.open(project_id="p", root=git_repo)
    event = pv.record_verification(command="pytest", passed=True)
    assert event.commit_sha == git(git_repo, "rev-parse", "HEAD")
    assert event.branch in {"main", "master"}
    pv.close()


def test_recall_labels_applicability(git_repo: Path) -> None:
    pv = Provalume.open(project_id="p", root=git_repo)
    pv.record_verification(command="pytest -q", passed=False, excerpt="E boom",
                           error_kind="e")
    results = pv.recall("boom", limit=5).results
    assert results
    assert results[0].explanation.applicability in set(Applicability)
    pv.close()


def test_provalume_never_writes_to_the_repository(git_repo: Path) -> None:
    """Read-only means read-only: no commits, no checkouts, no config changes."""
    before_head = git(git_repo, "rev-parse", "HEAD")
    before_status = git(git_repo, "status", "--porcelain")
    before_branch = git(git_repo, "rev-parse", "--abbrev-ref", "HEAD")

    pv = Provalume.open(project_id="p", root=git_repo)
    pv.record_verification(command="pytest", passed=True)
    pv.record_decision(selected="x")
    pv.recall("pytest", limit=5)
    pv.audit()
    pv.rebuild()
    pv.close()

    assert git(git_repo, "rev-parse", "HEAD") == before_head
    assert git(git_repo, "rev-parse", "--abbrev-ref", "HEAD") == before_branch
    # The only new path should be .provalume/, which is gitignored in real use.
    after_status = git(git_repo, "status", "--porcelain")
    added = set(after_status.splitlines()) - set(before_status.splitlines())
    assert all(".provalume" in line for line in added), f"unexpected changes: {added}"
