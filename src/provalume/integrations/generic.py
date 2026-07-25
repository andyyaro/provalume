"""Generic integration helpers: digest injection and context-file materialization.

Two jobs, both usable by any orchestrator:

1. **Splice a digest into a prompt.** One choke point, every agent, no per-vendor
   code.
2. **Materialize vendor context files, and remove them again.** Opt-in, because
   the cleanup contract is what makes it safe and cleanup is easy to get wrong
   (ADR-0015).

The materialization rules exist because a real orchestrator was verified to run
``git add -A`` over the whole worktree when committing an agent's work. Anything
written there lands in the agent's commit, the reviewer's diff, and the
integration branch — so cleanup is deterministic and explicit rather than
delegated to ``.gitignore``.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from provalume.policy.scope import confine
from provalume.schemas.retrieval import Digest

#: Written as the first line of every generated file, so a stray one is
#: identifiable as Provalume's rather than a user's.
SENTINEL: Final = "<!-- provalume:generated do-not-commit -->"

#: Vendor context files. Codex documents a 32 KB cap on AGENTS.md with silent
#: truncation, so truncation here is Provalume's explicit decision rather than a
#: vendor's quiet one.
VENDOR_FILES: Final[dict[str, tuple[str, int]]] = {
    "codex": ("AGENTS.md", 32 * 1024),
    "claude": ("CLAUDE.md", 32 * 1024),
    "gemini": ("GEMINI.md", 32 * 1024),
    "generic": ("AGENTS.md", 32 * 1024),
}

DEFAULT_VENDORS: Final[tuple[str, ...]] = ("codex", "claude", "gemini")


# --- Prompt splicing --------------------------------------------------------


def splice_digest(instructions: str, digest: Digest, *, heading: str = "") -> str:
    """Append a digest to an instruction string.

    The digest goes **after** the task instructions, not before. Putting
    retrieved memory first would give it the position of primary instruction,
    which is exactly the framing the untrusted-data banner exists to deny.

    An empty digest returns the instructions unchanged, so a caller need not
    branch on whether anything was recalled.
    """
    if not digest.items:
        return instructions

    section = heading or "## Prior context from Provalume"
    return f"{instructions}\n\n{section}\n\n{digest.text}\n"


def render_for_file(digest: Digest, *, limit: int) -> str:
    """Render a digest for a vendor context file, with the sentinel header.

    Truncation is explicit and announced. A silently truncated context file can
    cut mid-item and leave a memory's claim without its trust label, which is how
    an untrusted record starts reading like a fact.
    """
    body = f"{SENTINEL}\n\n# Prior context from Provalume\n\n{digest.text}\n"
    if len(body) <= limit:
        return body
    notice = "\n\n[truncated by Provalume to fit this file's size limit]\n"
    return body[: max(0, limit - len(notice))] + notice


# --- Materialization --------------------------------------------------------


@dataclass
class MaterializeResult:
    """What materialization actually did.

    ``written`` is what cleanup will remove — exactly those paths, never a glob,
    because a glob would delete a user's real ``CLAUDE.md``.
    """

    written: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)

    @property
    def any_written(self) -> bool:
        return bool(self.written)


def materialize(
    digest: Digest,
    worktree: Path | str,
    *,
    vendors: Sequence[str] = DEFAULT_VENDORS,
) -> MaterializeResult:
    """Write vendor context files into a worktree.

    Returns exactly what was written. A file that already exists is **skipped**,
    never overwritten: overwriting a user's committed context file is destructive
    and silent, and a crash between overwrite and restore would lose their
    content outright.

    Paths are confined to the worktree root.
    """
    root = Path(worktree).resolve()
    result = MaterializeResult()

    if not digest.items:
        return result

    for vendor in vendors:
        spec = VENDOR_FILES.get(vendor)
        if spec is None:
            continue
        filename, limit = spec
        target = confine(filename, root)

        if target.exists():
            result.skipped.append(target)
            continue

        target.write_text(render_for_file(digest, limit=limit), encoding="utf-8")
        result.written.append(target)

    return result


def cleanup(written: Sequence[Path]) -> list[Path]:
    """Remove generated files. Returns what was actually removed.

    Operates on the exact path list from :func:`materialize`, and refuses to
    delete a file whose first line is not the sentinel. That refusal is the
    defence against a path-list mismatch removing something real.
    """
    removed: list[Path] = []
    for path in written:
        if not path.exists():
            continue
        try:
            first = path.read_text(encoding="utf-8").split("\n", 1)[0].strip()
        except OSError:  # pragma: no cover - unreadable file
            continue
        if first != SENTINEL:
            # Not ours. Something replaced it, or the list is wrong.
            continue
        path.unlink()
        removed.append(path)
    return removed


@contextmanager
def materialized(
    digest: Digest,
    worktree: Path | str,
    *,
    vendors: Sequence[str] = DEFAULT_VENDORS,
) -> Iterator[MaterializeResult]:
    """Materialize for the duration of a block, then clean up.

    Cleanup runs on exception too. A crashed task must not leave a generated file
    behind for a subsequent ``git add -A`` to sweep into a commit — which is the
    exact failure this whole mechanism exists to prevent.

        with materialized(digest, worktree) as files:
            run_the_agent()
        # files are gone here, before anything stages
    """
    result = materialize(digest, worktree, vendors=vendors)
    try:
        yield result
    finally:
        cleanup(result.written)


def generated_paths(worktree: Path | str) -> list[Path]:
    """Every Provalume-generated file currently in a worktree.

    A safety net for callers that lost their path list — a crash between
    materialize and cleanup, say. Identifies files by sentinel, so it can never
    return a user's own context file.
    """
    root = Path(worktree).resolve()
    found: list[Path] = []
    for filename, _ in VENDOR_FILES.values():
        candidate = root / filename
        if not candidate.exists():
            continue
        try:
            first = candidate.read_text(encoding="utf-8").split("\n", 1)[0].strip()
        except OSError:  # pragma: no cover
            continue
        if first == SENTINEL and candidate not in found:
            found.append(candidate)
    return found


def assert_clean(worktree: Path | str) -> None:
    """Raise if any generated file remains. Call before staging.

    Belt to the context manager's braces: a caller that materialized without the
    context manager can still refuse to commit a polluted worktree.
    """
    remaining = generated_paths(worktree)
    if remaining:
        names = ", ".join(p.name for p in remaining)
        msg = (
            f"Provalume-generated file(s) still present in {worktree}: {names}. "
            "They must be removed before staging, or they will enter the agent's "
            "commit and the reviewer's diff."
        )
        raise RuntimeError(msg)
