# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [semantic versioning](https://semver.org/spec/v2.0.0.html), with the
pre-1.0 caveat that `0.x` minor bumps may break the SDK.

## [Unreleased]

Found by continued dogfooding. The shared shape: every unit test passed, every
event recorded, and the feature still did nothing — or did the wrong thing — in
a real run.

### Fixed

**Imported events bypassed admission.** `import_records()` appended straight to
the journal, so a JSONL file was never redacted, never poisoning-scanned, and
never size-capped, and its own `redaction` / `integrity` blocks were adopted as
if Provalume had produced them — which meant a file could assert `risk: 0.0` and
switch off the promotion gate that reads it. Imported events now cross the same
admission boundary as a locally recorded one, and a record that fails admission
is reported as an import issue rather than raised. Threat T11.

**A file could withdraw the recipient's own memories.** `human.rejection`,
`human.invalidation` and `branch.rejected` acted on whatever their payload
named, with no check on the event's source and no check that the memory belonged
to the event's project. `rejected` is terminal, so a two-line export could
permanently withdraw a verified memory with no way back. Withdrawal now requires
a human or kernel source and a matching project. Threats T9, T17.

**Live performance aggregates disagreed with a rebuild.** `Projector.apply()`
built a fresh accumulator per event, so each write replaced the aggregate with
one event's counts: an agent with a 1-in-10 success rate was served, at
`verified`, as "1/1 succeeded (100%)". Only `rebuild` computed the real figure,
and `audit(deep=True)` could not see the difference. Aggregates now merge into
what is stored, idempotently.

**A success was recorded as the fix for unrelated failures.** Resolution was
inferred from nothing but a shared task or run, so the first passing gate in a
task was written into every other open gotcha as "What later worked" — a false
sentence, hashed into `content_hash`, that also closed the signature so the
genuine fix could never attach. Inference now requires the same command or the
same declared `purpose`, which is recorded on the gotcha.

**"What later worked" repeated the command that failed.** When the fix is the
same command passing later, naming it is tautological: the command is not what
changed. The warning now names the resolving commit, which was recorded all
along and surfaced nowhere. `PreflightMatch` carries `resolution_commit_sha` and
`resolved_at`.

## [0.1.1] — 2026-07-25

Found by dogfooding: driving a real orchestration run through failure, a real
fix, and success. Every item here was invisible to the test suite, the eval
harness, and code review.

### Fixed

**Cross-run resolution was unreachable.** The projector read
`resolves_signature` from a verification payload and used it to link a fix to
the failure it resolved, across runs. Nothing could write it — not the SDK, not
the adapters, not a test. The only reachable path inferred resolution within a
single task or run, but the real recovery path is to block a task, escalate to a
human, and do the work in a *later* run, so a failure and its fix always landed
in different runs. `what_later_worked` was therefore always empty and a fixed
failure warned forever. `record_verification()` now accepts
`resolves_signature`.

**A resolved failure read as an open one.** The pre-action warning opened with
"A similar approach failed previously" even when it carried what fixed it —
inviting an agent to avoid an approach that now works. Resolved matches say so
in the headline.

**The gate could not be consulted without recording.** `warning.shown` means a
warning was shown to someone, and it is the denominator for warning usefulness.
Anything needing to look up a signature had to inflate that count.
`OrkestraAdapter.preflight()` now forwards `record=False`.

**An empty listing explained nothing.** `events`, `memories` and `recall`
printed nothing when the project id did not match what the database held, which
is indistinguishable from "nothing was ever recorded". They now name the
projects present and the flag that selects one.

### Added

- `Journal.project_ids()` — the distinct project ids in a journal.
- `record_verification(resolves_signature=...)`.
- `OrkestraAdapter.preflight(record=...)`.

### Notes

Each fix carries a regression test verified by reintroducing the bug and
confirming the test fails. `docs/reference/LIMITATIONS.md` records what the run
established and what it still does not.

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
