# ADR-0007: Deterministic writers, no LLM in the write path

**Status:** Accepted · **Date:** 2026-07-25

## Context

Every memory engine reviewed — Mem0, Letta, Zep/Graphiti, Cognee, MemOS, LangMem,
claude-mem — puts a language model in the write path. Text goes in, a model extracts
or compresses, and the result is stored.

For Provalume that is disqualifying, for a reason specific to its claim rather than
a general dislike of models:

1. **Non-reproducibility.** The same inputs produce different stored facts on
   different runs. "This fact was proved by this evidence" is unverifiable if
   re-deriving the fact from the evidence yields something else.
2. **Quality depends on an accident.** Point an engine at whatever local model the
   user happens to run and memory quality becomes a property of their hardware.
3. **Interpretation of hostile text is a poisoning primitive.** An extractor reading
   attacker-controlled repository content is the attack in
   [`MEMORY_POISONING.md`](../security/MEMORY_POISONING.md), with an LLM
   volunteering to launder it.
4. **It inverts the trust story.** A system claiming determinism cannot have a
   non-deterministic writer at the layer users trust least.

## Decision

**No LLM is required, or used, anywhere in the canonical write path.** Memory is
written by deterministic functions of structured events.

### Event → memory mappings

| Event | Produces |
|---|---|
| verification failure | `gotcha` candidate, with a computed failure signature |
| repeated failure signature | elevated repeated-attempt warning on the existing gotcha |
| successful repair after a failure | resolution linked to the gotcha |
| review rejection | `gotcha` lesson candidate |
| later review approval of the same subject | resolution evidence on that lesson |
| human decision | `decision` memory, `source=human` |
| deterministic command success | `procedural` candidate keyed on the exact command |
| run completion | `episodic` projection |
| landed integration | eligibility for `semantic` / `procedural` promotion |
| rejected attempt | negative experience; **never** current project truth |
| repository fact changed | invalidation or supersession of the prior fact |

Each mapping is a pure function from event to candidate: same event in, same record
out, byte for byte. Tested by asserting `content_hash` equality across repeated
derivation, which is also what makes `provalume rebuild` meaningful.

### Failure signatures

The mechanism behind "repeated failure". A signature is SHA-256 over a normalised
tuple of `(command, error_kind, error_fingerprint)`, where normalisation strips
absolute paths, line/column numbers, hex digests, timestamps, PIDs, durations, port
numbers, and temp-directory names.

This is deliberately lossy. Two failures with the same signature are *probably* the
same failure, not certainly. Too aggressive and distinct failures collide; too
timid and the same failure never matches itself. The normalisation rules are
documented and individually tested in
[`docs/reference/PREFLIGHT.md`](../reference/PREFLIGHT.md), and false-positive rate
is an eval metric (scenario 19) rather than an assumption.

### Agents propose; they do not promote

Agents may submit candidates through the SDK or MCP `propose`. Those land
`quarantined`. Promotion is a separate operation requiring deterministic evidence
([ADR-0005](ADR-0005-trust-lifecycle.md)). The party producing a claim is never the
party granting it trust.

### Where an LLM is allowed, eventually

Strictly optional, additive, and never on the canonical path:

- Idle-time distillation that produces *additional* summary records, clearly marked
  as LLM-derived and capped at `observed`. Roadmap, not 0.1.0.
- A user asking an agent to draft a proposal — which then enters `quarantined` like
  any other agent input.

If a distillation pass never runs, nothing breaks. That is the test for whether an
LLM feature is correctly optional: removing it must degrade compactness, never
correctness.

## Consequences

**Good.** Reproducible writes. `rebuild` produces byte-identical projections, which
makes the journal-as-source-of-truth claim testable rather than aspirational. No API
key, no network, no model download for core function. Tests are exact-equality
assertions instead of tolerance windows. Hostile text is never interpreted, only
recorded.

**Bad.** Provalume cannot distil prose into a fact. If evidence arrives only as an
agent's paragraph, the paragraph is what gets stored — `quarantined`, unpromotable.
This is a real capability gap versus LLM-extraction engines, and it is the correct
side of the trade for a system whose claim is provenance.

**Bad.** Integrations must emit *structured* events. An integration that can only
scrape stdout gets weak memory. This pushes work onto integrators, deliberately:
[ADR-0014](ADR-0014-orkestra-integration-boundary.md) requires structured records
over prose scraping.

**Also bad.** Failure-signature normalisation is a heuristic with a false-positive
rate. Measured, not hidden.

## Alternatives rejected

**LLM extraction with a deterministic fallback.** The fallback becomes the untested
path, and stored content varies by whether a model was available — so two databases
from identical runs disagree.

**LLM extraction gated behind an opt-in flag, writing to the same tier.** Same
reproducibility loss, now conditional and harder to reason about. If it ever ships
it writes to a separate, clearly-marked, capped tier.

**Require agents to emit structured JSON and trust it.** Structure is not evidence.
A well-formed lie is still a lie; see
[`MEMORY_POISONING.md`](../security/MEMORY_POISONING.md) §2.2.
