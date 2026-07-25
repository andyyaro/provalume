# Lifecycle guide

How a memory gets from "an agent said something" to "the project proved this",
and what stops it.

Specification: [`TRUST_MODEL.md`](../security/TRUST_MODEL.md).

---

## The ladder

```
quarantined ──▶ observed ──▶ verified ──▶ reviewed ──▶ integrated
   agent          reported     a command    a non-author    it landed
   prose          from a run   returned     approved it     in history
```

Plus three terminal states — `invalidated`, `superseded`, `rejected` — which are
retained as history and never promoted.

**Rungs are never skipped.** `observed → integrated` in one step is refused even
when an integration event exists, because the intervening evidence would not have
been recorded and the audit trail would be true by accident and incomplete in
fact.

## A worked example

```python
# 1. An agent proposes. Lands quarantined. Nothing can change that.
pv.propose(text="Integration tests need to run serially", agent="agent-A")
#    → quarantined
```

Nothing promotes it, because nothing supports it yet.

```python
# 2. A verification runs and fails.
pv.record_verification(
    command="pytest -n auto tests/integration",
    passed=False,
    excerpt="E TimeoutError: deadlock in db fixture teardown",
    error_kind="test_failure",
    task_id="t1",
)
#    → a gotcha, at verified
#      rule: promote.gotcha.verified_by_failure_evidence
```

The gotcha reaches `verified` because a deterministic failure event supports it.
Its `verification_state` is `failed`. Both are true.

```python
# 3. Something else works.
pv.record_verification(
    command="pytest -p no:xdist tests/integration",
    passed=True, purpose="the integration suite", task_id="t1",
)
#    → a procedure, at verified
#      rule: promote.procedural.verified_by_exact_command
#    → the gotcha gains a resolution link
```

The procedure is keyed on the exact command. Evidence for a *different* command
would not have promoted it.

```python
# 4. Someone who is not the author approves.
pv.record_review(reviewer="reviewer-2", approved=True, task_id="t1")
#    → the approval is stamped on the procedure and kept as its evidence
#    → still at verified
```

The rung is not granted here. A procedure cannot pass `verified` without landed
history (see the ceilings below), so the approval is recorded and *used* at the
next step rather than acted on immediately.

Every approval is kept, not only the first. Had `agent-A` approved its own work
before `reviewer-2` did, both approvals would be evidence, and the promotion
below would still find the independent one.

```python
# 5. It lands.
pv.record_integration(commit_sha="a1b2c3…", target="user", task_id="t1")
#    → the procedure climbs the rungs its evidence now supports
#      verified  → reviewed    rule: promote.any.independent_review_approved
#      reviewed  → integrated  rule: promote.any.landed_in_history
```

Had the *only* approval come from `agent-A`, the record's own author, the first
of those two steps would have been refused with `refuse.self_review` — and the
refusal recorded.

The gotcha stays at `verified`. What landed was the fix; the failure is still a
failure.

## Reading the history

```sh
provalume explain <memory-id> --transitions
```

```
  ok  observed  -> verified     promote.procedural.verified_by_exact_command
  ok  verified  -> reviewed     promote.any.independent_review_approved
  ok  reviewed  -> integrated   promote.any.landed_in_history
```

Each row names the rule and lists the evidence events it relied on. That naming
is what makes the model falsifiable rather than decorative.

## Refusals are recorded too

```
  refused  observed -> verified   refuse.no_qualifying_evidence
  refused  quarantined -> observed  refuse.agent_cannot_promote
```

A promotion attempt that vanishes silently is exactly what an attacker wants.

## Per-category ceilings

| Type | Ceiling without landed history | Notes |
|---|---|---|
| `episodic` | `verified` | `integrated` is meaningless — the episode happened regardless |
| `semantic` | `reviewed` | **Needs `integrated` to be current truth** |
| `procedural` | `verified` | Needs the *exact* command to have passed |
| `decision` | `integrated` | Directly, when `source=human` |
| `gotcha` | `verified` | Never promoted to semantic truth |
| `performance` | `verified` | A statistic does not land in a commit |

Two categories have no command to run, so `observed → verified` asks for what
does settle them:

- `semantic` — the landing itself, or a recorded human decision.
  Rule: `promote.semantic.landed_or_human_authority`. The landing has to be the
  record's own; an unrelated integration event is not evidence about this fact.
- `decision` — the human decision event, at every rung.
  Rule: `promote.decision.human_authority`.

Everything else needs a verification or command-result event from a source
trusted to report a deterministic outcome.

## Withdrawal

Three ways a record stops being current, and they mean different things:

```python
pv.invalidate(memory_id, reason="the dependency was removed")
#    → invalidated. It stopped being true; no replacement asserted.

pv.supersede(old_id, statement="The project uses uv.", subject="package manager")
#    → the old record is superseded; both persist, linked.

pv.reject(memory_id, actor="operator", reason="disproved")
#    → rejected. Permanent. Retained as negative experience.
```

"We no longer use pip" and "we use uv now" are different claims. Conflating them
loses the *reason* a fact changed, which is the part a later reader needs.

### The one way back

An `invalidated` record can return to `verified` if fresh deterministic evidence,
recorded *after* the invalidation, shows the fact holds again — a reverted revert,
a restored dependency. Rule: `revalidate.invalidated.fresh_evidence`.

`superseded` and `rejected` have no equivalent. Supersession is resolved by
writing a new record; rejection is permanent. Without that asymmetry there would
be a laundering path from rejected work back to trusted truth.

## Contradictions

Two current semantic records in the same scope, same subject key, differing
content:

```sh
provalume recall "runtime" --explain
```

```
warning: contradicted by another current record; neither is auto-resolved
```

Detected, never resolved. Recency is not correctness — the newer record may be
the poisoned one. Resolve it yourself with `invalidate` or `supersede`.

## What you cannot do

- Promote a `rejected` record. Ever.
- Promote as an agent. No SDK path, CLI path, or MCP tool allows it.
- Skip a rung.
- Approve your own work into `reviewed`.
- Serve a semantic record as current truth without landed history.
- Widen scope to `global` — the capability does not exist in 0.1.0.

Each is asserted by a test in `tests/security/`.
