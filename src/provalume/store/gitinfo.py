"""Read-only Git access for commit validity (ADR-0006).

Provalume **never writes to a repository**. No commits, no checkouts, no config
changes, no worktree creation, no fetches. Every call here is a read.

The one rule this module exists to serve:

    A query as of commit X must not present a fact introduced after X as
    current truth.

And the one rule it exists to *avoid* breaking: never fabricate certainty from
topology. Git ancestry answers "could this fact have been true here?", not "is
this fact true here?" — a file the fact described may have been rewritten by an
unrelated commit, a cherry-pick creates a different SHA for the same change, and
a rebase rewrites history wholesale. So when ancestry cannot be established the
answer is :attr:`Applicability.UNCERTAIN`, freely and without embarrassment.

If ``git`` is absent, or the path is not a repository, everything degrades to
``UNCERTAIN`` and scope-only filtering. That is a real loss of capability, and it
is reported by ``provalume doctor`` rather than hidden.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - read-only git invocations, never shell=True
from pathlib import Path

from provalume.schemas.scope import Applicability

#: Every subprocess call is bounded. A pathological repository must slow a query,
#: never hang one.
_TIMEOUT_S = 10.0


class GitUnavailable(Exception):
    """No usable repository. Callers degrade rather than fail."""


class GitInfo:
    """Read-only repository queries, with per-instance caching.

    Caching is per-instance and unbounded on purpose: a ``GitInfo`` lives for the
    duration of one query or one projection pass, where the same handful of
    commits are asked about repeatedly. A long-lived instance would need eviction;
    none is created.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else Path.cwd()
        self._available: bool | None = None
        self._ancestor_cache: dict[tuple[str, str], bool | None] = {}
        self._exists_cache: dict[str, bool] = {}

    # -- availability ------------------------------------------------------

    @property
    def available(self) -> bool:
        """Whether a usable Git repository is present."""
        if self._available is None:
            self._available = self._probe()
        return self._available

    def _probe(self) -> bool:
        if shutil.which("git") is None:
            return False
        try:
            result = self._run(["rev-parse", "--is-inside-work-tree"])
        except GitUnavailable:
            return False
        return result.strip() == "true"

    def _run(self, args: list[str]) -> str:
        """Run a git command. ``check=False`` so a non-zero exit is data.

        Never uses ``shell=True`` and never interpolates a caller value into a
        command string; arguments are passed as a list.
        """
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, no shell  # nosec B603 B607
                ["git", "-C", str(self.root), *args],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            msg = f"git invocation failed: {exc}"
            raise GitUnavailable(msg) from exc
        if completed.returncode != 0:
            msg = f"git {' '.join(args)} exited {completed.returncode}: {completed.stderr.strip()}"
            raise GitUnavailable(msg)
        return completed.stdout

    # -- queries -----------------------------------------------------------

    def current_branch(self) -> str | None:
        if not self.available:
            return None
        try:
            branch = self._run(["rev-parse", "--abbrev-ref", "HEAD"]).strip()
        except GitUnavailable:
            return None
        return None if branch in {"", "HEAD"} else branch

    def current_commit(self) -> str | None:
        if not self.available:
            return None
        try:
            return self._run(["rev-parse", "HEAD"]).strip() or None
        except GitUnavailable:
            return None

    def repository_id(self) -> str | None:
        """A stable identifier for this repository.

        Prefers the first remote URL, because that is stable across clones and
        across a directory being renamed. Falls back to the toplevel directory
        name, which is not stable but is better than nothing.
        """
        if not self.available:
            return None
        try:
            remotes = self._run(["remote"]).split()
            if remotes:
                url = self._run(["remote", "get-url", remotes[0]]).strip()
                if url:
                    return url
        except GitUnavailable:
            pass
        try:
            return Path(self._run(["rev-parse", "--show-toplevel"]).strip()).name or None
        except GitUnavailable:
            return None

    def commit_exists(self, sha: str) -> bool:
        """Whether a commit object is present.

        A garbage-collected or never-fetched commit returns ``False``, which is
        not evidence of forgery — only of absence. Callers must treat it as
        unresolvable rather than as a failed check (threat T15).
        """
        if not self.available or not sha:
            return False
        if sha in self._exists_cache:
            return self._exists_cache[sha]
        try:
            self._run(["cat-file", "-e", f"{sha}^{{commit}}"])
            result = True
        except GitUnavailable:
            result = False
        self._exists_cache[sha] = result
        return result

    def is_ancestor(self, ancestor: str, descendant: str) -> bool | None:
        """Whether ``ancestor`` is reachable from ``descendant``.

        Returns ``True``/``False`` when the question could be answered, and
        ``None`` when it could not — either commit missing, no repository, or a
        git failure. The tri-state is the point: collapsing ``None`` into
        ``False`` would silently turn "I cannot tell" into "definitely not",
        which after any rebase would make memory go quiet exactly when it is
        needed.
        """
        if not self.available or not ancestor or not descendant:
            return None
        if ancestor == descendant:
            return True
        key = (ancestor, descendant)
        if key in self._ancestor_cache:
            return self._ancestor_cache[key]

        if not (self.commit_exists(ancestor) and self.commit_exists(descendant)):
            self._ancestor_cache[key] = None
            return None

        try:
            subprocess.run(  # noqa: S603 - fixed argv, no shell  # nosec B603 B607
                ["git", "-C", str(self.root), "merge-base", "--is-ancestor", ancestor, descendant],
                capture_output=True,
                timeout=_TIMEOUT_S,
                check=True,
            )
            result: bool | None = True
        except subprocess.CalledProcessError as exc:
            # Exit 1 is a definitive "no". Anything else is a failure to answer.
            result = False if exc.returncode == 1 else None
        except (OSError, subprocess.TimeoutExpired):
            result = None
        self._ancestor_cache[key] = result
        return result

    def branch_contains(self, sha: str, branch: str) -> bool | None:
        """Whether ``branch`` contains ``sha``."""
        if not self.available or not sha or not branch:
            return None
        try:
            head = self._run(["rev-parse", branch]).strip()
        except GitUnavailable:
            return None
        return self.is_ancestor(sha, head)


def applicability_at(
    *,
    record_commit: str | None,
    query_commit: str | None,
    git: GitInfo | None,
    scope_applicability: Applicability,
) -> tuple[Applicability, str]:
    """Decide whether a record is current truth at the queried commit.

    Returns the applicability and a human-readable reason, because the reason is
    what ``explain`` shows and what makes an ``UNCERTAIN`` answer useful rather
    than merely evasive.

    The ordering below is the rule from ADR-0006, in code:

    1. no ``commit_sha`` on the record — not commit-anchored, judge by scope;
    2. no ``commit_sha`` on the query — nothing to compare against;
    3. ancestor of the query commit — potentially valid here;
    4. *not* an ancestor — introduced elsewhere or later, so not current truth;
    5. ancestry unanswerable — ``UNCERTAIN``, never assumed valid.
    """
    if not record_commit:
        return scope_applicability, "record is not anchored to a commit"

    if not query_commit:
        return (
            Applicability.UNCERTAIN,
            f"record is anchored to {record_commit[:12]} but the query named no commit",
        )

    if record_commit == query_commit:
        return scope_applicability, f"recorded at this exact commit {query_commit[:12]}"

    if git is None or not git.available:
        return (
            Applicability.UNCERTAIN,
            "no repository available, so commit ancestry could not be checked",
        )

    ancestry = git.is_ancestor(record_commit, query_commit)
    if ancestry is True:
        return (
            scope_applicability,
            f"{record_commit[:12]} is an ancestor of {query_commit[:12]}",
        )
    if ancestry is False:
        return (
            Applicability.HISTORICAL,
            f"{record_commit[:12]} is not an ancestor of {query_commit[:12]}, so it was "
            "introduced on another line of history",
        )
    return (
        Applicability.UNCERTAIN,
        f"ancestry between {record_commit[:12]} and {query_commit[:12]} could not be "
        "determined — the commit may have been rebased, cherry-picked, or "
        "garbage-collected",
    )
