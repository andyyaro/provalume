# ADR-0010: Memory poisoning controls

**Status:** Accepted · **Date:** 2026-07-25

## Context

The research report's sharpest criticism of its own recommendation was that memory
poisoning was unmodelled and is "your differentiator's Achilles heel". It is right.
Shared cross-agent memory creates a durable channel from attacker-controlled
repository content into a future agent's prompt.

The attack detail is in [`MEMORY_POISONING.md`](../security/MEMORY_POISONING.md).
This ADR records the *architectural* commitments, so that a future change which
would quietly re-open the channel has something concrete to violate.

## Decision

**Poisoning resistance is an architectural property, not a filter.** Three tiers,
in order of how much weight each carries.

### Tier 1 — architecture (carries the load)

These are the controls that actually work, because they do not depend on
recognising hostile text:

1. **Deterministic evidence is the only promotion path.** A claim without
   qualifying evidence cannot become trusted, regardless of how well written it is.
   This single property defeats the hardest case — the confident, clean, false
   statement — which no heuristic can catch.
2. **`source` is structural.** Assigned by the code path that created the event,
   never derived from content. A payload claiming `"verified": true` is payload.
3. **Agents never promote.** Producer and truster are always different parties.
4. **The MCP surface contains no promotion tool.** Absent, not disabled. Asserted by
   test, so adding one fails CI.
5. **Scope containment by default.** A poisoned record stays in the branch and run
   it came from; widening requires evidence or human approval.
6. **Semantic project truth requires landed history.** The highest-value target
   needs the hardest evidence.
7. **Rejection is terminal.** No laundering path.
8. **Retrieved memory is data.** Banner, per-item trust labels, provenance inline.

### Tier 2 — heuristics (reduce exposure earlier)

A bounded `poisoning_risk` score in `[0, 1]`, computed at admission from pattern
families: instruction override, AI-directed address, self-asserted trust,
dangerous shell constructs, credential steering, role confusion.

| Effect | Threshold |
|---|---|
| Forced to `quarantined`, promotion refused | `poisoning_risk ≥ 0.5` (configurable) |
| Ranking penalty `−0.60 × risk` | any non-zero risk |

Which patterns matched is **recorded**, so `explain` can show why a record was
penalised. An opaque penalty would be untunable and unfalsifiable.

Scoring is additive per matched family with a per-family cap, so one record matching
six families saturates rather than producing an unbounded score. Deterministic:
the same text always scores the same, which is required for the eval harness.

**These are heuristics and are treated as such.** They have false positives, they
miss novel phrasings, and they are never the only control on any path.

### Tier 3 — containment (limits damage after a miss)

Hard digest budget · result and candidate caps · size caps at admission · MCP rate
limiting · full transition auditability including refusals · rebuildable journal ·
invalidation without deletion.

### Measured, not asserted

Eval scenario 11 runs adversarial records through the real admission, promotion, and
retrieval code and reports **poisoning success rate**: the fraction reaching a trust
state above `observed`, or appearing unlabelled in a digest.

**Target zero. A non-zero result is a bug, not a tuning parameter.** The measured
baseline is committed under `evals/results/baseline/`.

### Changes that require security review

Listed in [`CONTRIBUTING.md`](../../CONTRIBUTING.md) because each would re-open the
channel:

- an LLM in the write path
- exposing promotion, invalidation, or supersession to MCP
- letting payload influence its own trust state
- cross-project or global promotion without human approval
- making vectors the authorisation gate
- serving semantic records as current truth without landed history

## Consequences

**Good.** The defence does not depend on out-guessing an attacker's phrasing. The
architecture makes the attack unprofitable rather than merely harder. Poisoning
resistance is measurable and regression-tested.

**Bad.** False positives. A legitimate procedural memory containing `chmod 777` in a
diagnostic note will be flagged and quarantined, requiring explicit promotion. Real
friction, on the correct side.

**Bad.** Tier 1 means agents cannot self-serve. An agent that learns something true
cannot make it trusted without an external evidence event. Deliberate, and the whole
point.

**Also bad.** The residual instruction-following risk (T4) is not eliminated.
Provalume cannot force a model to honour the untrusted-data banner. Stated in the
threat model §7, in the poisoning document §2.4, and in
[`LIMITATIONS.md`](../reference/LIMITATIONS.md) — three places, because it is the
weakest point and a reader deserves to find it without digging.

## Alternatives rejected

**Heuristics as the primary defence.** What a filter-first design produces: an arms
race against phrasing, losable by one novel formulation. Heuristics are Tier 2 for a
reason.

**An LLM classifier to detect poisoning.** Asks a model to judge attacker-controlled
text — the attack surface, again, one layer up. Also non-deterministic, so the same
record could be admitted on Tuesday and rejected on Wednesday.

**Trusting structured input.** Structure is not evidence.

**Human review of every write.** Would work and would make the system unusable.
Human approval is required only where it is load-bearing: cross-scope promotion.
