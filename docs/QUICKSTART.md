# Quickstart

Sixty seconds to a useful memory. No API key, no agent CLI, no network.

## Install

```sh
uv tool install provalume      # or: pipx install provalume, pip install provalume
```

## See it work

```sh
provalume demo
```

Runs a complete scenario in a temporary directory using the real storage, policy,
and retrieval code — an agent fails, the gotcha is recorded, a second agent is
warned, a fix is verified and independently reviewed, a procedure is promoted, a
stale fact is superseded, and a later query retrieves it all with provenance.

Add `--html report.html` for a light-themed report you can open in a browser.

## Use it in a project

```sh
cd your-project
provalume init      # creates .provalume/ (add it to .gitignore — provalume does not commit)
provalume doctor    # checks Python, SQLite, FTS5, Git, permissions
```

Nothing is recorded yet. Provalume stores evidence, so something has to produce
evidence:

```python
from provalume import Provalume

pv = Provalume.open()          # project_id derives from the Git remote

# A verification failed. This is the evidence a gotcha is built from.
pv.record_verification(
    command="pytest -n auto tests/integration",
    passed=False,
    excerpt="E   TimeoutError: deadlock in db fixture teardown",
    error_kind="test_failure",
)
```

Now ask before repeating it:

```python
warning = pv.preflight(command="pytest -n auto tests/integration")
if warning.matched:
    print(warning.summary)
```

```
A similar approach failed previously.

  Previous attempt   pytest -n auto tests/integration
  Failure evidence   E   TimeoutError: deadlock in db fixture teardown
  What later worked  (nothing recorded yet)
  Applicability      current
  Provenance         branch main; commit 4f2a91c8e0d1
  Trust state        verified
  Match confidence   1.00 (exact failure signature match)

  This is a warning, not a block. Provalume does not override policy.
```

Record what worked, and the gotcha learns its own resolution:

```python
pv.record_verification(command="pytest -p no:xdist tests/integration", passed=True)
```

## Retrieve

```sh
provalume recall "integration tests" --explain
provalume recall "integration tests" --digest 2000    # what an agent would receive
```

From Python:

```python
digest = pv.recall("integration tests").digest(char_budget=2000)
print(digest.text)      # always banner-first, always within budget
```

## Check it holds up

```sh
provalume status    # what this database contains
provalume audit     # prove the chain, the projections, the pragmas, the redaction
provalume explain <memory-id> --transitions
```

`explain` is the point of the whole system. If it cannot tell you why a record is
trusted, the record should not be trusted.

## Connect an agent

```sh
provalume serve-mcp                # read tools plus propose
provalume serve-mcp --read-only    # read tools only — for shared environments
```

An MCP client can recall, explain, query failures and decisions, and run the
preflight gate. It can *propose* memories, which land quarantined. It cannot
promote, invalidate, supersede, or delete: those tools are not on the surface at
all ([ADR-0012](adr/ADR-0012-mcp-permissions.md)).

## Getting real value

Provalume only knows what you record. The high-value events, in rough order:

| Record this | With | Because |
|---|---|---|
| Verification results | `record_verification(...)` | The evidence everything else is built on |
| Review verdicts | `record_review(reviewer=..., approved=...)` | Independent review is a promotion rung |
| Landed commits | `record_integration(commit_sha=...)` | What semantic truth requires |
| Human decisions | `record_decision(selected=..., rejected=[...])` | Stops agents re-proposing what you rejected |
| Repository facts | `record_fact(subject=..., statement=...)` | Superseded rather than overwritten when they change |

Wiring these into an orchestrator is what
[`docs/integration/ORKESTRA.md`](integration/ORKESTRA.md) describes.

## Where to go next

| | |
|---|---|
| [Trust model](security/TRUST_MODEL.md) | What "verified" means, precisely |
| [Retrieval](reference/RETRIEVAL.md) | The ranking policy, with every constant |
| [Preflight](reference/PREFLIGHT.md) | How failure signatures work |
| [Limitations](reference/LIMITATIONS.md) | Read before adopting |
