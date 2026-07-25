# Orkestra integration

Orkestra is a local-first orchestration runtime for multiple autonomous coding
agents (PyPI: `orkestra-runtime`). It is Provalume's reference integration,
because it is the first system that can supply real verification evidence.

**Status: a draft pull request, not a released integration.** It is tested against
fixtures and runs Orkestra's own test suite; it has not run against production
traffic.

Boundary decision: [ADR-0014](../adr/ADR-0014-orkestra-integration-boundary.md).

---

## The dependency direction

```
Orkestra ──optionally──▶ Provalume
```

**Provalume's core imports nothing from Orkestra**, and the adapter imports
nothing from Orkestra either — it accepts plain dictionaries shaped like
Orkestra's records. A test asserts both.

That makes the adapter testable with fixtures alone, and it means Orkestra takes
Provalume as an *optional* dependency rather than the reverse. Without Provalume
installed, Orkestra behaves exactly as it did before.

## Wiring it up

```python
from provalume import Provalume
from provalume.integrations.orkestra import (
    OrkestraAdapter, OrkestraContext, is_available, safe_digest, safe_preflight,
)

if is_available():
    pv = Provalume.open(project_id=config.project_name)
    memory = OrkestraAdapter(
        pv,
        OrkestraContext(
            project_id=config.project_name,
            repository_id=repo.remote_url,
            run_id=run.run_id,
            branch=workspace.branch,
            base_commit=workspace.base_commit,
        ),
    )
```

## What to record

Structured records, never prose scraping. An adapter that parsed agent output
would be doing extraction — interpretation of possibly-hostile text — which is
the poisoning primitive this design refuses.

| Orkestra produces | Call | Why it matters |
|---|---|---|
| A verification result | `memory.verification(...)` | **The single most valuable thing.** Failures become gotchas; successes become procedures. |
| A review verdict | `memory.review_verdict(...)` | Independent review is a promotion rung |
| A reviewer finding | `memory.reviewer_finding(...)` | Repeated findings become retrievable lessons |
| A resolved decision gate | `memory.human_decision(...)` | Stops agents re-proposing rejected alternatives |
| An integration commit | `memory.integration_landed(...)` | What semantic truth requires |
| A revert | `memory.integration_reverted(...)` | Invalidates what it landed |
| An abandoned branch | `memory.branch_rejected(...)` | Its records stop being truth, stay as experience |
| An attempt result | `memory.attempt_completed(...)` | Feeds performance memory |
| A task or run outcome | `memory.task_completed(...)` / `run_completed(...)` | Episodic history |

### Review verdicts need an explicit call

Orkestra keeps verdicts inside `attempts.result` JSON — there is no `reviews`
table (verified by reading its schema at v0.4.4). So the adapter takes verdicts
as an explicit call at the point the verdict is produced, rather than reading a
table that does not exist.

The `reviewer` field matters: it is compared against the record's author, and a
self-review never promotes.

## Where to inject

### The task brief — the primary path

`Orchestrator._render_brief()` in `src/orkestra/kernel/scheduler.py` builds the
single instructions string every adapter receives. **One splice there reaches
every agent with no per-vendor code**, and it depends on no unverified vendor
behaviour.

```python
from provalume.integrations.generic import splice_digest

digest = safe_digest(memory, query=task.spec.title, char_budget=2000)
if digest is not None:
    instructions = splice_digest(instructions, digest)
```

The digest is appended **after** the task instructions. Putting retrieved memory
first would give it the position of primary instruction, which is exactly the
framing the untrusted-data banner exists to deny.

### Before dispatch and before each retry

```python
warning = safe_preflight(memory, command=task.spec.acceptance[0] if task.spec.acceptance else "")
if warning is not None and warning.matched:
    fix_context = f"{fix_context}\n\n{warning.summary}"
```

**Memory never overrides policy.** The gate returns a warning Orkestra may
surface; it cannot block a dispatch, change a retry budget, or veto an
assignment. A memory-poisoning bug must not become an orchestration-control bug.

### Director planning

Supply relevant decisions and gotchas to the planning input the same way.

## Failure semantics

Chosen per mode, because "fail open" and "fail closed" are each wrong in the
other's case.

| Failure | Behaviour | Why |
|---|---|---|
| Retrieval fails or times out | **Fail open**, warn | Memory is an enhancement; an outage must not stop a run |
| Provenance corruption for a record | **Fail closed for that record** | Serving a record whose provenance cannot be resolved would break the core claim. Drop the record, not the run. |
| Promotion fails mid-transaction | Nothing promoted | A partial promotion would be a trusted record with no transition row |
| Database unavailable or corrupt | Run continues without memory | An Orkestra run must never be corrupted by Provalume |
| Provalume not installed | Orkestra behaves exactly as before | Optional means optional |

`safe_digest` and `safe_preflight` implement fail-open. Provenance fail-closed is
inside retrieval, not the caller's problem.

## The generated-file trap

`WorkspaceManager.commit_workspace()` calls `add_all_and_commit()` — a
`git add -A` over the whole worktree, **verified at `workspace/worktrees.py:100`**.
Any file written there lands in the agent's commit, the reviewer's diff, and the
integration branch.

So materialization is opt-in and its cleanup is deterministic:

```python
from provalume.integrations.generic import materialized, assert_clean

with materialized(digest, workspace.path) as files:
    result = await run_the_agent(brief)
# files are gone here

assert_clean(workspace.path)          # belt and braces
await workspace_manager.commit_workspace(workspace, message)
```

The contract:

1. `materialize` returns **exactly** the paths it wrote; cleanup uses that list,
   never a glob — a glob would delete a user's real `CLAUDE.md`.
2. A pre-existing file is **skipped, never overwritten**. A crash between
   overwrite and restore would lose their content outright.
3. Written files carry a sentinel header, and cleanup **refuses** to delete a file
   that lacks it.
4. The context manager cleans up on exception, so a crashed task leaves nothing
   behind.
5. `.gitignore` is defence in depth, not the mechanism.

`tests/security/test_integration_boundary.py` runs the regression that matters:
materialize, exit the block, `git add -A`, and assert no generated file was
staged.

### Vendor pickup is unverified

Provalume claims only that files are written where asked and removed before
staging. It does **not** claim that any particular CLI reads them.

Two facts were flagged as resting on secondary sources — Antigravity's native
`AGENTS.md` support, and the effect of the Claude adapter's `--setting-sources
user` flag on `CLAUDE.md` auto-loading inside worktrees. Neither was verified, so
neither is depended on. The brief splice needs neither
([`RESEARCH_VALIDATION.md`](../research/RESEARCH_VALIDATION.md) §2).

## Constraints inherited

Verified against Orkestra v0.4.4:

| Constraint | Provalume |
|---|---|
| Python ≥ 3.12 | same |
| mypy strict | same |
| Apache-2.0 | same |
| stdlib `sqlite3`, WAL, no ORM | same |
| Redaction at write | its own implementation — the core must not import a host |
| Small dependency footprint | three runtime dependencies |

Provalume implements its own redaction rather than importing `orkestra.redact`.
That is duplication, and it is the correct kind: importing it would create exactly
the coupling this boundary exists to prevent. The cost is that two rule sets can
drift; Provalume's are tested independently.

## What is not done

- **Not merged.** A draft PR only.
- **No Orkestra release** is published from this work.
- **Not dogfooded.** Fixture-tested, not production-proven — which is the first
  item in [`LIMITATIONS.md`](../reference/LIMITATIONS.md).
- **Vendor context-file pickup untested**, as above.

## Testing the adapter

```sh
uv run pytest tests/security/test_integration_boundary.py -q
```

Covers the full ladder through the adapter, error-kind mapping, performance
aggregation, branch rejection, fail-open behaviour, the import boundary, and the
`git add -A` cleanup contract.
