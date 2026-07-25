# ADR-0014: Orkestra integration boundary

**Status:** Accepted · **Date:** 2026-07-25

## Context

Orkestra is a local-first orchestration runtime for multiple autonomous coding
agents (PyPI distribution `orkestra-runtime`, currently 0.4.4, Apache-2.0). It is the
first system that can supply Provalume with real verification evidence, and it is
Provalume's reference integration.

Reference integration is not the same as host. If Provalume's core knew about
Orkestra's types, scheduler, or config, the dependency would invert and Provalume
would become an Orkestra component that happens to live in another repository.

## Decision

**Provalume's core never imports Orkestra. The Orkestra adapter depends on
Provalume. Provalume is optional to Orkestra at runtime.**

### Dependency direction

```
schemas and policies
      ↓
immutable event journal
      ↓
deterministic projections
      ↓
memory lifecycle
      ↓
retrieval and context composition
      ↓
SDK / CLI / MCP
      ↓
integrations  ←── the only layer that may know about a host
```

`src/provalume/integrations/orkestra.py` translates Orkestra's native records into
Provalume events. It imports **nothing** from Orkestra — it accepts plain dicts and
dataclass-shaped inputs, so it is testable with fixtures and does not require
Orkestra installed. `tests/security/test_no_orkestra_import.py` asserts no module
outside `integrations/` references Orkestra, and that `integrations/orkestra.py`
itself has no `import orkestra`.

### What is ingested, as structured events

Runs · tasks · assignments · attempts · verification results · review verdicts ·
reviewer findings · human decisions · integration commits · accepted results ·
agent/profile outcomes.

**Structured records, never prose scraping** ([ADR-0007](ADR-0007-deterministic-writers.md)).
Verified against Orkestra's actual schema: `runs`, `tasks`, `task_deps`, `attempts`,
`events`, `decisions`, `observations`, `ledger`, `workspaces`, `usage_log`.

One gap found by reading the source: **review verdicts live only inside
`attempts.result` JSON — there is no `reviews` table.** So the adapter accepts review
verdicts as explicit structured input rather than expecting to read them from a
table, and the integration emits them at the point the verdict is produced.

### Injection points

| Point | Mechanism | Verified |
|---|---|---|
| Task brief | Splice a budgeted digest into `Orchestrator._render_brief()` | Yes — `src/orkestra/kernel/scheduler.py:757`, called at `:691`; its return becomes `TaskBrief.instructions`, which every adapter receives |
| Director planning | Supply relevant memory to planning input | Yes — `src/orkestra/director/` exists |
| Pre-dispatch and retry | Query the preflight gate before dispatch and before each retry | Yes — retry logic in `src/orkestra/kernel/retry.py` |

The brief splice is the primary path because it reaches every adapter with no
per-adapter code.

### Failure semantics

Chosen per failure mode rather than uniformly, because "fail open" and "fail closed"
are each wrong in the other's case:

| Failure | Behaviour | Why |
|---|---|---|
| Retrieval fails or times out | **Fail open** with a warning | Memory is an enhancement. A memory outage must not stop a run. |
| Provenance corruption detected for a memory | **Fail closed for that memory** | Serving a record whose provenance cannot be resolved would break the core claim. Drop the record, not the run. |
| Promotion fails mid-transaction | Nothing promoted | Partial promotion would create a trusted record with no transition row. |
| Memory database unavailable or corrupt | Run continues without memory | An Orkestra run must never be corrupted by Provalume. |
| Provalume not installed | Orkestra behaves exactly as before | Optional means optional. |

### Policy authority

**Memory never overrides Orkestra policy.** The preflight gate returns a warning
that Orkestra may surface; it cannot block a dispatch, override a retry budget,
change an assignment, or veto a policy decision. Orkestra's policy engine remains
the only authority. A memory system that could override policy would be a
memory system that an attacker could use to override policy.

### Generated context files

Orkestra's `WorkspaceManager.commit_workspace()` calls `add_all_and_commit()`, i.e.
`git add -A` over the whole worktree — **verified at `workspace/worktrees.py:100`**.
Any injected `AGENTS.md` / `CLAUDE.md` / `GEMINI.md` would be swept into the agent's
commit and pollute the reviewer's diff and the integration branch.

So: generated files are removed **deterministically before staging**, by an explicit
cleanup call, not by relying on `.gitignore`.
[ADR-0015](ADR-0015-worktree-materialization.md) specifies the mechanism;
`.gitignore` is defence in depth only.

### Release discipline for this session

The Orkestra integration lands as a **draft pull request** against a separate clone
at `~/Downloads/Provalume-Orkestra-Integration`. The active checkout at
`~/Downloads/Orkestra` is treated as strictly read-only — another session holds write
access. No Orkestra release is published, and the PR is not merged.

## Consequences

**Good.** Provalume is independently useful and independently testable. Orkestra
takes no mandatory dependency. Both can release on their own schedules. The
integration is a worked reference for other orchestrators, not a special case.

**Bad.** Duplication: Provalume implements its own redaction rather than importing
`orkestra.redact`, and its own ID generation. Correct — importing them would create
the coupling this ADR exists to prevent — and it means two rule sets can drift.
Provalume's are its own, tested independently.

**Bad.** The adapter must be kept in step with Orkestra's schema by hand. Mitigated
by fixture-based tests that fail loudly on shape changes rather than silently
producing empty memory.

**Also bad.** Without dogfooding inside a real Orkestra release, the integration is
verified against fixtures and a rebased test run, not against production traffic.
Stated in [`LIMITATIONS.md`](../reference/LIMITATIONS.md).

## Alternatives rejected

**Build inside Orkestra, extract later** — the research report's recommendation.
Overridden by direction; accounted for in
[`RESEARCH_VALIDATION.md`](../research/RESEARCH_VALIDATION.md) §1. The architectural
argument against it: extraction is exactly how hidden couplings form.

**Make Provalume a required Orkestra dependency.** Every Orkestra user would inherit
Provalume's failure modes for a feature many do not want.

**Have Provalume read Orkestra's database directly.** Couples to another project's
schema across a version boundary, and reads state without the structured semantics
that make promotion decidable.

**Let memory influence scheduling decisions.** Turns a memory-poisoning bug into an
orchestration-control bug.
