# ADR-0015: Worktree context-file materialization

**Status:** Accepted · **Date:** 2026-07-25

## Context

Agent CLIs read context from vendor-specific files: Codex reads `AGENTS.md`
(documented 32 KB cap with silent truncation), Claude Code reads `CLAUDE.md` and
does **not** read `AGENTS.md`, Gemini CLI reads `GEMINI.md`. Writing a digest into
those files is the obvious way to reach an agent that Provalume cannot otherwise
influence.

Two problems. First, the research report flagged two facts as resting on secondary
sources — Antigravity's native `AGENTS.md` support, and the effect of the Claude
adapter's `--setting-sources user` flag on `CLAUDE.md` auto-loading inside worktrees
— and recommended verifying both before building on them. **Neither was verified**
(see [`RESEARCH_VALIDATION.md`](../research/RESEARCH_VALIDATION.md) §2), because
verifying them requires invoking real vendor CLIs against a real worktree.

Second, the verified trap: Orkestra's `commit_workspace()` runs `git add -A` over the
whole worktree (`workspace/worktrees.py:100`). Any file written there lands in the
agent's commit, the reviewer's diff, and the integration branch.

## Decision

**Materialization is opt-in, secondary to the prompt splice, and its cleanup is
deterministic rather than `.gitignore`-dependent.**

### Prompt splice is primary

The digest reaches agents through the task brief
([ADR-0014](ADR-0014-orkestra-integration-boundary.md)) — one choke point, every
adapter, no per-vendor behaviour, and **no dependency on either unverified fact.**
Provalume 0.1.0 is fully functional with materialization never enabled.

### The cleanup contract

Materialization exposes two functions and a context manager:

```python
materialize(digest, worktree, *, vendors) -> list[Path]   # returns exactly what it wrote
cleanup(worktree, *, written) -> list[Path]               # removes exactly those paths
with materialized(digest, worktree, vendors=…) as paths:  # cleanup guaranteed on exit
    ...
```

Rules that make this safe:

1. **`materialize` returns the exact paths it wrote.** Cleanup operates on that list,
   never on a glob — a glob would delete a user's real `CLAUDE.md`.
2. **A pre-existing file is never overwritten.** If `CLAUDE.md` already exists,
   materialization **skips** it and reports the skip. Overwriting a user's committed
   context file is destructive and silent.
3. **Written files carry a sentinel header** so a stray file is identifiable as
   Provalume-generated:
   ```
   <!-- provalume:generated do-not-commit -->
   ```
4. **`cleanup` refuses to delete a file lacking the sentinel.** Defence against a
   path-list mismatch removing something real.
5. **Cleanup runs before staging**, called explicitly by the integration, not left to
   `.gitignore`. `.gitignore` is defence in depth.
6. **The context manager guarantees cleanup on exception**, so a crashed task cannot
   leave a file behind for `git add -A`.
7. **Paths are confined to the worktree root.** Traversal outside is rejected (threat
   T21).
8. **Content is size-capped per vendor** — 32 KB for `AGENTS.md`, matching Codex's
   documented cap, so truncation is Provalume's explicit decision rather than a
   silent vendor one.

`tests/integration/test_materialization.py` asserts: a written file is removed
before staging; a pre-existing file is preserved; a missing sentinel prevents
deletion; an exception mid-task still cleans up; and — the regression test that
matters — **a simulated `git add -A` after cleanup stages no generated file.**

### Unverified vendor behaviour is documented as unverified

`docs/integration/ORKESTRA.md` states plainly which vendor auto-loading behaviours
are verified and which are not. Provalume does not claim that writing `AGENTS.md`
causes any particular CLI to read it. It claims only that the file is written where
asked, and removed before staging.

**No integration support is claimed without testing it.** Materialization's tested
guarantee is the write-and-cleanup contract, not vendor pickup.

## Consequences

**Good.** A capability for agents outside the brief path, without betting on
unverified facts. Cleanup is verified against the actual failure mode. A user's real
context files are safe.

**Bad.** Opt-in means most users never use it, so it will get less real-world
exercise than the brief splice. Accepted — the alternative is a default that can
pollute commits.

**Bad.** Without vendor verification, users must test pickup themselves. Documented
rather than papered over.

**Also bad.** Skipping pre-existing files means a project that already commits
`CLAUDE.md` gets no materialized digest for that vendor. Correct precedence:
the user's file wins.

## Alternatives rejected

**Materialize by default.** Any bug in cleanup pollutes commits and reviewer diffs.

**Rely on `.gitignore`.** `git add -A` respects `.gitignore` — until someone
force-adds, or the worktree has a different ignore configuration, or a nested
repository shadows it. Deterministic removal does not depend on that.

**Glob-based cleanup** (`rm worktree/{AGENTS,CLAUDE,GEMINI}.md`). Deletes the user's
real files. The path-list plus sentinel exists to make that impossible.

**Overwrite existing files and restore afterwards.** A crash between overwrite and
restore loses the user's content. Skipping cannot lose data.

**Claim vendor support based on documentation.** Documentation is not a test, and
the report explicitly flagged these two facts as unverified.
