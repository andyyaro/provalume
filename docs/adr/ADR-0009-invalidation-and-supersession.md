# ADR-0009: Invalidation and supersession, never overwrite

**Status:** Accepted · **Date:** 2026-07-25

## Context

Coding facts churn. "The project uses `pip`" becomes false the day someone lands
`uv`. The obvious implementation — `UPDATE memories SET text = …` — destroys the
answer to "what did we believe at commit X?", which is a feature, and to "when did
this change, and on what evidence?", which is the audit trail.

Zep/Graphiti's bi-temporal schema is the right idea and needs no LLM: it is a
schema decision, not an extraction technique.

## Decision

**Facts are never overwritten and never hard-deleted. They are invalidated or
superseded.**

### Bi-temporal fields

| Field | Meaning |
|---|---|
| `valid_at` | when the fact became true in the world |
| `invalid_at` | when it stopped being true (`NULL` while current) |
| `recorded_at` | when Provalume learned it |

Two axes, because they genuinely differ: a fact can be *recorded* today about
something that became true last month, and a query can ask either "what was true
then?" or "what did we know then?".

### Invalidation versus supersession

**Invalidation** — the fact stopped being true and no replacement is asserted.
`invalid_at` set, `trust_state → invalidated`. Retained; retrievable for historical
queries.

**Supersession** — a specific newer record replaces it. The new record's
`supersedes_id` points at the old one; the old one's `trust_state → superseded`.
Both persist and the chain is walkable in both directions.

The distinction is not pedantry: "we no longer use `pip`" and "we use `uv` now" are
different claims. The first is a deletion of knowledge, the second a substitution.
Conflating them loses the *reason* a fact changed.

### Chain rules

- Chains are **linear**: one record has at most one direct successor. Two records
  claiming to supersede the same predecessor is a **conflict**, surfaced, never
  resolved by last-write-wins.
- Cycles are refused at write time.
- Depth is bounded (default 64) so a corrupt chain cannot hang a query.
- Following a chain to its head is a single recursive CTE.

### What triggers each

| Event | Result |
|---|---|
| Repository fact changed, new fact recorded | supersession |
| Fact contradicted with no replacement | invalidation |
| Verification that previously passed now fails | invalidation of the procedural record |
| Reviewer rejects a previously-approved claim | `rejected` (terminal, not invalidation) |
| Commit that established a fact is reverted | invalidation, `integration_state → reverted` |
| Human `provalume invalidate` | invalidation, `source=human` |

### Contradiction detection

Detected, not resolved. Two `semantic` records in the same scope covering the same
subject with differing content, both current, are marked as a contradiction pair.
Effects: a ranking penalty (`p_contradiction`), a digest warning, and an `audit`
report entry.

Deliberately conservative — subject matching is on a normalised subject key, not
semantic similarity, so it misses paraphrased contradictions. Detecting *more* would
mean interpreting text, which means an LLM in the read path, which is
[ADR-0007](ADR-0007-deterministic-writers.md) territory. A missed contradiction is
a lower-ranked warning; a *fabricated* contradiction would demote a correct fact.

### The narrow re-validation path

An `invalidated` record can return to a ladder state if fresh deterministic
evidence shows the fact holds again — a reverted revert, a restored dependency. It
requires a new transition row with rule `revalidate.invalidated.fresh_evidence`.

`superseded` and `rejected` have no such path: supersession is resolved by writing a
new record, and rejection is permanent. Without this asymmetry there would be a
laundering route from `rejected` back to trusted.

## Consequences

**Good.** History survives. Time-travel queries work. "Why did this change?" has an
answer with evidence attached. Contested facts are visible rather than silently
resolved.

**Bad.** The database only grows. Every superseded version is retained forever.
Acceptable for text-sized records; growth is measured in
[`PERFORMANCE.md`](../reference/PERFORMANCE.md).

**Bad.** Queries are more complex — every current-truth path filters `invalid_at`
and terminal states. Centralised in the retrieval layer so no caller can forget.

**Also bad.** No hard deletion. Combined with the append-only journal, this makes
Provalume unsuitable for data under a deletion requirement
([`PRIVACY_MODEL.md`](../security/PRIVACY_MODEL.md) §6). The remedies are to delete
the database or to export-filter-reimport.

## Alternatives rejected

**Overwrite in place.** Destroys the audit trail and historical queries — the two
things the append-only journal exists to provide.

**Soft delete with a `deleted` boolean.** Cannot distinguish "no longer true" from
"replaced by this specific thing" from "was rejected". Three different facts.

**Auto-resolve contradictions by recency.** Recency is not correctness. The newer
record may be the poisoned one.

**LLM-based semantic contradiction detection.** An LLM in the read path,
interpreting content that may be hostile.
