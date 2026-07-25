# ADR-0005: Eight trust states, ladder plus terminal

**Status:** Accepted · **Date:** 2026-07-25

## Context

"Verified memory" needs a mechanical definition of verified, or it is marketing.
A single boolean `verified` flag cannot express the distinctions that matter:
a command passed in one worktree, versus a reviewer approved it, versus it landed
in `main`. Those are three different amounts of trust and they license three
different presentations.

Meanwhile a record can be *withdrawn* — invalidated, superseded, or rejected — and
withdrawal is not a lower rung on the same ladder. Asking "is `rejected` more
trusted than `observed`?" is a category error.

## Decision

**Eight states in two shapes: a five-rung ladder and three terminal states.**

```
LADDER (ranked, comparable)
  quarantined(1) → observed(2) → verified(3) → reviewed(4) → integrated(5)

TERMINAL (unranked, excluded from ordinary retrieval, never promoted)
  invalidated    superseded    rejected
```

The full state semantics, per-transition evidence requirements, per-category rules,
source ceilings, and invariants are specified in
[`TRUST_MODEL.md`](../security/TRUST_MODEL.md). This ADR records why the shape is
what it is.

### Why two shapes rather than one ordering

A single ordering would force a rank onto withdrawal, which either makes
`invalidated` look like weak trust (and therefore retrievable as weak truth) or
makes `rejected` look like an extreme of trust. Both are wrong. Separating them
means `min_trust` filtering is a simple rank comparison, and exclusion of withdrawn
records is a separate, unambiguous set membership test.

### Why five rungs and not three

Each rung corresponds to a distinct evidence artifact that actually exists in an
orchestrated workflow:

| Rung | The artifact |
|---|---|
| `quarantined` | none — admitted but unsupported, or flagged |
| `observed` | a structured report tied to a real run |
| `verified` | a verification result (exit code) |
| `reviewed` | an independent verdict from a non-author |
| `integrated` | a resolvable commit that landed |

Collapsing `verified` and `integrated` would destroy the project's central claim
that **verification passing is not always enough for project truth**. Collapsing
`quarantined` and `observed` would remove the distinction between "an agent said
this" and "an agent said this and it tripped a poisoning heuristic".

### Trust state is separate from evidence state

Three orthogonal fields sit alongside: `verification_state`
(`unknown`/`passed`/`failed`), `review_state`
(`none`/`approved`/`changes_requested`/`rejected`), `integration_state`
(`none`/`integrated_run`/`accepted_user`/`reverted`).

The gotcha case forces this: `trust_state=verified` with
`verification_state=failed` is coherent and common — the evidence is real, and the
evidence is a failure. One collapsed field cannot say it.

### Rungs are never skipped

`observed → integrated` in one step is refused even when an integration event
exists. The intervening transitions would not have been recorded, and the audit
trail would be true by accident and incomplete in fact. Three transitions, three
evidence sets, three rows.

### Every transition is recorded, including refusals

`memory_transitions` records `from_state`, `to_state`, the **named** `policy_rule`
that authorised or refused it, `evidence_event_ids`, `actor`, `actor_source`,
`recorded_at`, and `scope`.

Recording refusals is a security property, not bookkeeping: a promotion attempt
that silently vanishes is exactly what an attacker wants.

## Consequences

**Good.** "Verified" has a checkable definition. `min_trust` is a one-line
comparison. Every trust claim is falsifiable through `provalume explain`.
Withdrawal is unambiguous.

**Bad.** Eight states is a real learning cost. The CLI defaults to `min_trust=observed`
and `explain` narrates state in prose, so the common path does not require holding
the model in your head.

**Also bad.** Three-step promotion means more transition rows than a single jump,
and more code paths to test. That is the cost of an audit trail that is complete
rather than plausible.

**Sharp edge.** A record can be `verified` while its `verification_state` is
`failed`. This reads as a contradiction until you know it is the gotcha case. It is
called out in the data-model reference and in `explain` output, because it will
otherwise be reported as a bug.

## Alternatives rejected

**A boolean `verified` flag.** Cannot distinguish command-passed from
reviewer-approved from landed. The distinction is the product.

**A 0.0–1.0 confidence score.** Confidence scores in agent memory are usually
model-assigned, which makes them exactly the self-asserted trust signal this
architecture refuses. A discrete state with named evidence is auditable; a float is
not.

**One flat ordering including terminal states.** Forces a meaningless rank onto
withdrawal.

**Letting terminal records be re-promoted freely.** Creates a laundering path from
`rejected` to trusted. Rejection is permanent; the one narrow re-validation path
from `invalidated` requires fresh deterministic evidence and its own logged rule.
