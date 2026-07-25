# ADR-0006: Branch and commit validity semantics

**Status:** Accepted · **Date:** 2026-07-25

## Context

Coding facts churn, and they churn *per branch*. Two agents in concurrent worktrees
can hold contradictory, simultaneously-correct beliefs: one migrated the project to
`uv` on its branch, the other still uses `pip` on `main`. Both are right, in scope.

No reviewed memory system models this. The closest, claude-mem's
`adopt --branch`, stamps merged worktrees into the parent project after the fact —
a reconciliation step, not a validity model. In every system reviewed, a fact
recorded on a branch that was later abandoned is just a fact.

The consequence Provalume must prevent: a query made while working at commit X
returning, as *current truth*, a fact introduced at commit Y > X, or a fact that
only ever existed on a rejected branch.

## Decision

**Scope and commit validity are first-class, filtered on every retrieval path.**

### Scope hierarchy

```
project → repository → branch → run → task → attempt → agent
```

Narrow scopes are served to matching contexts. Widening scope is a promotion
requiring its own evidence ([ADR-0005](ADR-0005-trust-lifecycle.md)):
branch → repository needs landed integration; repository → project needs explicit
human approval; project → global does not exist in 0.1.0.

### Git lifecycle positions

Modelled explicitly, because each licenses a different presentation:

| Position | Meaning |
|---|---|
| observed in worktree | recorded while an agent worked; branch-scoped |
| verified in worktree | a command passed there; still branch-scoped |
| independently reviewed | a non-author assessed it |
| integrated into run branch | merged into the run's integration branch |
| accepted into user branch | landed where the user actually works |
| invalidated by later commit | a subsequent commit made it false |
| superseded by later fact | a specific newer record replaces it |
| rejected with preserved lesson | terminal; retained as negative experience |

### The commit-validity rule

> A query as of commit X must not present a fact introduced after X as current
> truth.

Evaluation, in order:

1. If the record has no `commit_sha`, it is not commit-anchored. Applicability is
   judged by scope alone and labelled accordingly.
2. If `commit_sha` is an ancestor of X (or equal), the record is potentially valid
   at X.
3. If `commit_sha` is *not* an ancestor of X, the record is **not** current truth at
   X. It may still be returned as historical or cross-branch context — labelled.
4. If ancestry cannot be determined — no repository available, a bare checkout, a
   pruned or garbage-collected commit — applicability is
   **`uncertain`**, never assumed valid.

### Never fabricate certainty from topology

This is the rule that keeps the feature honest. Git ancestry answers *"could this
fact have been true here?"*, not *"is this fact true here?"*. A file the fact
described may have been rewritten by an unrelated commit; a cherry-pick creates a
different SHA for the same change; a rebase rewrites history wholesale.

So `applicability` is a reported field with four values — `current`,
`historical`, `cross_scope`, `uncertain` — and `uncertain` is used freely rather
than resolved by guessing. Where topology is genuinely ambiguous, the digest says
so and the retrieval explanation says why.

### Specific topologies

| Situation | Behaviour |
|---|---|
| Concurrent contradictory worktrees | Both records kept, both branch-scoped, contradiction detected and penalised, digest warns |
| Rejected branch | Records `rejected`; excluded from truth, retained as gotcha experience |
| Merge commit | Ancestry through both parents; a fact from either merged branch may be valid |
| Cherry-pick | New SHA, so ancestry fails; falls to `uncertain` rather than a false negative presented as fact |
| Rebase | Original SHAs unreachable; affected records become `uncertain`, and `audit` reports unresolvable provenance |
| Branch deleted | Records retained; ancestry unresolvable → `uncertain` |
| Commit garbage-collected | Same as above; the record is not silently promoted or dropped |

Cherry-pick and rebase are the honest weak spots: Provalume detects that it *cannot
tell* and says so. That is better than a confident wrong answer, and worse than a
content-level equivalence check, which 0.1.0 does not attempt.

### Git access

Read-only, via `git` subprocess calls (`merge-base --is-ancestor`, `cat-file -e`,
`rev-parse`), with results cached per query. Provalume never writes to a repository:
no commits, no checkouts, no config changes, no worktree creation. If `git` is
absent or the path is not a repository, everything degrades to `uncertain` and
scope-only filtering, and `doctor` reports it.

## Consequences

**Good.** The differentiator no reviewed system has. Rejected work cannot become
project truth. Concurrent worktrees are representable rather than a corruption.
Historical queries actually work.

**Bad.** Ancestry checks cost subprocess calls. Mitigated by caching per query and
by only checking candidates that survive other filters — measured in
[`PERFORMANCE.md`](../reference/PERFORMANCE.md).

**Bad.** Rebase and cherry-pick degrade to `uncertain`, which will read as a
regression to users who rebase constantly. The alternative — guessing — is worse.
Content-level equivalence is roadmap work.

**Also bad.** Without a repository present, a genuine capability is lost, not just
degraded. Documented rather than hidden.

## Alternatives rejected

**Ignore branches; treat all memory as project-wide.** What every reviewed system
does. It makes rejected-branch knowledge indistinguishable from landed fact, which
is the failure this project exists to prevent.

**Assume validity when ancestry is unknown.** Fails safe in the wrong direction:
the unknown cases are exactly the ones where a stale fact does damage.

**Refuse to return anything whose ancestry is unknown.** Fails safe in the other
wrong direction: after any rebase, memory would go silent. Labelling beats both.

**Require a Git repository.** Provalume should still be useful for a non-Git
project, degraded to scope-only truth.
