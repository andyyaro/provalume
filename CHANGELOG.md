# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [semantic versioning](https://semver.org/spec/v2.0.0.html), with the
pre-1.0 caveat that `0.x` minor bumps may break the SDK.

## [0.1.0] — 2026-07-25

First release. Verified, git-aware memory for autonomous software agents.

### Added

**Immutable event journal.** Append-only, enforced by SQLite triggers rather than
by convention. Canonical JSON serialisation, deterministic SHA-256 hashing, a
per-database hash chain, idempotent ingestion, and full projection rebuild. WAL,
linear forward-only migrations, and refusal to open a schema newer than the code
understands.

**Six memory categories** — episodic, semantic, procedural, decision, gotcha,
performance — each with its own promotion ceiling, recency half-life, and write
trigger.

**Eight trust states** in two shapes: a five-rung ladder (`quarantined` →
`observed` → `verified` → `reviewed` → `integrated`) and three terminal states
(`invalidated`, `superseded`, `rejected`). Rungs cannot be skipped, agents cannot
promote, self-review never promotes, and rejection is permanent. Every transition
records the named policy rule and the evidence it relied on — **including
refusals**.

**Deterministic writers.** No language model anywhere in the canonical path.
Verification failures become gotchas keyed on normalised failure signatures;
repeats fold into one record with an occurrence count; a later success attaches as
the resolution; review rejections become lessons; human decisions become decision
memory; passing commands become procedural candidates.

**Branch- and commit-aware truth.** Read-only Git access for ancestry and commit
existence. A query as of commit X does not present a fact introduced after X as
current truth, and where ancestry cannot be established — after a rebase or a
cherry-pick — applicability is labelled `uncertain` rather than guessed.

**Bi-temporal validity.** `valid_at` / `invalid_at` / `recorded_at`, with
invalidation and supersession instead of overwriting. Contradictions are detected
and surfaced, never auto-resolved.

**Explainable retrieval.** FTS5 and BM25 with a documented nine-component scoring
policy, every constant published in `docs/reference/RETRIEVAL.md`. Filters
authorise; scoring only reorders. Every result reports which filters it passed,
each score component and its contribution, human-readable reasons, and warnings.
Ordering is fully deterministic.

**Budgeted digests.** Hard character or estimated-token ceiling enforced by
construction, banner-first, per-item trust labels and provenance, near-duplicate
suppression, and a reported count of what did not fit.

**Pre-action warning gate.** Deterministic failure signatures with five match
tiers. Warns by default and never blocks unless an explicit policy, an exact
signature match, and repetition all coincide. Warning outcomes are recordable, so
usefulness is measurable rather than assumed.

**Memory-poisoning resistance** as an architectural property: deterministic
evidence as the only promotion path, structural `source`, scope containment,
terminal rejection, and an untrusted-data banner — backed by heuristics and
containment, in that order of importance.

**Redaction before every durable write**, with hashing afterwards so stored
hashes cover what is actually on disk. `provalume audit` re-scans stored content.

**JSONL interchange.** Byte-identical exports, merge-friendly ordering, and an
import path that recomputes hashes rather than trusting them, rejects future
record versions, refuses foreign projects by default, and surfaces divergent
supersession as a conflict. Optional HMAC-SHA256 and Ed25519 signatures, both
fail-closed.

**Stable Python SDK, full CLI, and an MCP server** implemented against protocol
revision `2025-11-25` using only the standard library. The MCP surface exposes
read tools plus `propose`; promotion, invalidation, supersession, and delete are
absent by design and pinned by a test.

**Offline demo** (`provalume demo`) running the real engine in under a minute,
with an optional light-themed HTML report.

**Twenty-scenario replayable eval harness** with committed baseline results.

**Optional experimental vector retrieval** with a stdlib `HashingEmbedder`
baseline so the vector path is CI-tested without any optional dependency, plus
model2vec and fastembed backends and reciprocal rank fusion. Vectors never
authorise a record.

**Orkestra adapter** with structured event ingestion, digest injection, and
deterministic cleanup of generated context files.

**Documentation**: 18 ADRs, a 26-threat threat model, trust model, poisoning
analysis, privacy model, data model, retrieval reference, preflight reference,
JSONL specification, MCP guide, benchmark methodology, and a limitations document
that leads with the largest known weakness.

### Known limitations

Not dogfooded on production runs — the schema comes from literature, a competitor
review, and a synthetic eval harness rather than from mined production failure
frequencies. No hard deletion, no access control, no cross-project memory, no
verified vendor context-file pickup. Retrieved memory cannot be forced to stay
data. Full list: `docs/reference/LIMITATIONS.md`.

### Not claimed

No benchmark comparison against any other system. No LongMemEval-V2 score. No
superiority claim of any kind. `docs/reference/BENCHMARKS.md` explains what is
measured and what is not.

[0.1.0]: https://github.com/andyyaro/provalume/releases/tag/v0.1.0
