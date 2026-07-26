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

import contextlib
import re
import shutil
import subprocess  # nosec B404 - read-only git invocations, never shell=True
from collections.abc import Iterable
from pathlib import Path
from typing import Final

from provalume.schemas.scope import Applicability

#: Distinguishes "cached None" from "not cached" in the changed-files cache.
_UNSET: Final = object()

#: Every subprocess call is bounded. A pathological repository must slow a query,
#: never hang one.
_TIMEOUT_S = 10.0

#: ``git version 2.39.5 (Apple Git-154)`` -> ``2.39.5``. Linear by construction:
#: every repetition of the group must consume a literal ``.``, so no input can be
#: split two ways (the ReDoS shape guarded in tests/security/test_redos.py).
_VERSION = re.compile(r"\d+(?:\.\d+)*")

#: The only shape a revision handed to plumbing may take. `rev-list`'s
#: trailing `--` ends the pathspec, not option parsing, so a revision that
#: begins with `-` would be read as a flag; a hex-only gate closes that
#: before git ever sees it (M2 feeds this from a hook).
_HEX_SHA = re.compile(r"^[0-9a-fA-F]{4,64}$")


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
        self._changed_cache: dict[str, tuple[str, ...] | None] = {}
        self._file_cache: dict[tuple[str, str], str | None] = {}
        self._version_cache: str | None = None

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
            # Pinned encoding: a blob's decoding must not depend on the
            # machine's locale, or the same journal replayed on two hosts
            # would diverge. surrogateescape never raises, so an undecodable
            # blob becomes text `ast.parse` rejects — escalation, not a crash.
            completed = subprocess.run(  # noqa: S603 - fixed argv, no shell  # nosec B603 B607
                ["git", "-C", str(self.root), *args],
                capture_output=True,
                encoding="utf-8",
                errors="surrogateescape",
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
        # No remote configured, or git refused: fall through to the directory
        # name rather than failing. A repository without a remote is normal.
        with contextlib.suppress(GitUnavailable):
            remotes = self._run(["remote"]).split()
            if remotes:
                url = self._run(["remote", "get-url", remotes[0]]).strip()
                if url:
                    return url
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

    def changed_files(self, sha: str) -> tuple[str, ...] | None:
        """The paths ``sha`` touched — sorted, de-duplicated, repository-relative.

        Three cases, and the differences between them are the whole point:

        - **Ordinary commit** — the diff against its one parent.
        - **Merge commit** — the diff against its **first** parent, which for an
          integration merge is what "what did this landing change" means. Every
          other reading is worse: the default plumbing answer for a merge is
          *nothing at all*, diffing against the second parent would attribute
          the whole trunk to the merge, and diffing against all parents at once
          would attribute the whole side branch to it.
        - **Root commit** — nothing to diff against, so every file in it.

        Names are whatever git reports. Plumbing does no rename detection (the
        ``diff.renames`` default is porcelain-only), so a rename appears as both
        its old and its new path, and a deletion's path still counts as touched
        — code that referred to a file is very much affected by its removal.

        Paths are re-anchored to this instance's ``root``: git reports them
        relative to the repository *toplevel*, and when the client's root is a
        subdirectory the two differ. Changes outside the root are dropped —
        they cannot be expressed in the root's coordinate system, and nothing
        anchored there can refer to them.

        Returns ``None`` when the question could not be answered — no
        repository, a revision that is not plain hex, unknown or unreadable
        commit, git failure — and ``()`` for a commit that genuinely changed
        nothing in this root's world (``git commit --allow-empty``, or a
        commit touching only paths outside a subdirectory root). The two are
        kept apart because a caller deciding between "nothing to do" and
        "degrade" needs to tell them apart.
        """
        if not self.available or not sha or not _HEX_SHA.match(sha):
            return None
        cached = self._changed_cache.get(sha, _UNSET)
        if cached is not _UNSET:
            return cached  # type: ignore[return-value]
        try:
            # One call that both resolves the revision and names its parents:
            # `rev-list --parents -n 1` prints "<commit> <parent>...". The
            # trailing `--` ends the revision list, so a revision that also
            # names a file cannot be read as a pathspec.
            described = self._run(["rev-list", "--parents", "-n", "1", sha, "--"]).split()
        except GitUnavailable:
            self._changed_cache[sha] = None
            return None
        if not described:
            self._changed_cache[sha] = None
            return None

        commit, parents = described[0], described[1:]
        # `-z` keeps a path containing a space, a quote, or a newline intact;
        # without it git C-quotes such names and the caller would compare a
        # quoted string against a real path forever.
        args = ["diff-tree", "--no-commit-id", "--name-only", "-r", "-z"]
        args += [parents[0], commit, "--"] if parents else ["--root", commit, "--"]
        try:
            output = self._run(args)
        except GitUnavailable:
            self._changed_cache[sha] = None
            return None
        result = self._anchored(path for path in output.split("\0") if path)
        self._changed_cache[sha] = result
        return result

    def _anchored(self, paths: Iterable[str]) -> tuple[str, ...] | None:
        """Toplevel-relative git paths, re-anchored to this instance's root."""
        try:
            toplevel = Path(self._run(["rev-parse", "--show-toplevel"]).strip()).resolve()
        except GitUnavailable:
            return None
        base = Path(self.root).resolve()
        if toplevel == base:
            return tuple(sorted(set(paths)))
        anchored: set[str] = set()
        for path in paths:
            absolute = toplevel / path
            if absolute.is_relative_to(base):
                anchored.add(absolute.relative_to(base).as_posix())
        return tuple(sorted(anchored))

    def parents(self, sha: str) -> tuple[str, ...] | None:
        """The commit's parent shas, first parent first. ``None`` when the
        revision is not plain hex or cannot be read; ``()`` for a root
        commit."""
        if not self.available or not sha or not _HEX_SHA.match(sha):
            return None
        try:
            described = self._run(["rev-list", "--parents", "-n", "1", sha, "--"]).split()
        except GitUnavailable:
            return None
        if not described:
            return None
        return tuple(described[1:])

    def file_at(self, sha: str, path: str) -> str | None:
        """The file's text at a commit, or ``None``.

        ``None`` covers every shape of "cannot answer": bad revision, a path
        absent at that commit (added or deleted files), an unreadable blob.
        The sha is hex-gated and the path rides inside the fused ``sha:path``
        revision argument after ``--``-free plumbing, so neither can be read
        as an option. The ``./`` prefix makes git resolve the path relative
        to this instance's root (the ``-C`` directory) rather than the repo
        toplevel — radius paths are root-relative, and without the prefix a
        client rooted in a subdirectory could never read its own files.
        Cached per (sha, path): one scan asks about the same pre/post images
        once per record that shares a radius path, and the answer at a fixed
        commit never changes.
        """
        if not self.available or not sha or not _HEX_SHA.match(sha):
            return None
        key = (sha, path)
        cached = self._file_cache.get(key, _UNSET)
        if cached is not _UNSET:
            return cached  # type: ignore[return-value]
        try:
            result: str | None = self._run(["show", f"{sha}:./{path}"])
        except GitUnavailable:
            result = None
        except Exception:
            # Belt over the encoding pin: a blob the differ cannot read is a
            # file it cannot clear.
            result = None
        self._file_cache[key] = result
        return result

    def git_version(self) -> str | None:
        """The numeric version of the git executable, e.g. ``"2.39.2"``.

        Recorded alongside anything git extracted, so a result produced by one
        git can be told from a result produced by another. ``None`` when git
        cannot be run or prints something with no version in it. Cached per
        instance, like every other query here: an instance does not outlive
        the pass it serves, and the record path asks on every verification.
        """
        if self._version_cache is not None:
            return self._version_cache
        if not self.available:
            return None
        try:
            reported = self._run(["--version"])
        except GitUnavailable:
            return None
        match = _VERSION.search(reported)
        self._version_cache = match.group(0) if match else None
        return self._version_cache


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
