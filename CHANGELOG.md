# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [semantic versioning](https://semver.org/spec/v2.0.0.html), with the
pre-1.0 caveat that `0.x` minor bumps may break the SDK.

## [Unreleased]

### Fixed

**The MCP transport's nesting bound is its own, not the interpreter's.** A
deeply nested JSON line was refused because `json.loads` raised `RecursionError`
on it — a bound borrowed from CPython's stack rather than held by the server.
CPython 3.14.7 on Linux parses 100k-deep input without overflowing, so the same
attacker-controlled line stopped being a parse error and became "message is not
an object" instead. `handle_line` now checks nesting against `MAX_NESTING_DEPTH`
(100, where the protocol itself asks for 3 or 4) before parsing, so the answer no
longer depends on which interpreter is running. Brackets inside string literals
are not counted, or stored content would be refused for looking like an attack.

## [0.1.4] — 2026-07-25

Found by an independent reviewer running the Orkestra integration, then by
verification agents re-running the result. The theme: a record that is *false*
is worse than one that is missing, because the feature exists to be trusted.

### Changed

**A resolution is claimed at the landing, not at the verification that passed.**
A pass proves a command succeeded in some worktree, and an orchestrator discards
worktrees for merge conflicts, rejected reviews, exhausted retry budgets, and
tasks whose commit turns out empty. In each case the pass is real and the work
still vanishes — so a failure could be marked fixed by work nobody would ever
see again. `record_integration()` now accepts `resolves_signature`, and the
projector honours it, because a landing is the first event that proves the tree
changed.

Failure evidence still records eagerly at verification time: a failing attempt
genuinely failed, and repetition is exactly what elevates a warning from "this
once failed" to "this keeps failing".

### Fixed

**A landing named no command, so "what later worked" rendered empty** and read
as "(nothing recorded yet)" even though the link was recorded correctly. A
landing's answer is a commit: it now reads "work landed on <branch> as commit
<sha>".

**An integration event wrote its branch only into the payload,** so the event
envelope fell back to whatever branch the recording process had checked out. The
row read as though the work landed on `main` while the payload correctly named
the integration branch, and a consumer reading the column drew the opposite
conclusion.

**Every landing re-anchored every record on its branch,** leaving them all
wearing the last commit to land, whichever task produced it. Promotion is
branch-wide — a landing does make the branch's claims promotable — but
`commit_sha` says which commit a record is true of, and only one task's work
rode in any given commit. A record is now re-anchored only by its own task's
landing, and an integrated record carries what landed rather than the base its
worktree branched from.

### Notes

`LIMITATIONS.md` gains §9a: a failure signature is keyed on the command and the
error, not on the task, so under a repo-wide verification gate a sibling task's
landing can resolve a signature a still-blocked task shares. Only landed work
can resolve anything, which is enforced and tested; the residual is stated
rather than left implicit.

## [0.1.3] — 2026-07-25

Found by an independent reviewer who ran the Orkestra integration rather than
reading it. Both fixes here are boundaries the code claimed and did not enforce.

### Fixed

**A pre-action warning replayed captured command output with no untrusted-data
label.** The warning quotes stderr verbatim in its "Failure evidence" row and
serves it to an agent that never ran the command — the most
attacker-influenceable text Provalume handles, since any test, linter or build
tool writes it freely. The digest has carried a banner for exactly this threat
(T4) since 0.1.0; this channel is the same threat through a different door.
`PREFLIGHT_BANNER` now leads every warning, ahead of any quoted evidence, so a
reader that stops early has still seen the label. Fixed here rather than in the
caller so every consumer of the gate gets the boundary.

Mitigation, not prevention — Provalume cannot force a model to honour a label,
as LIMITATIONS §2 has always said.

### Added

- `PREFLIGHT_BANNER`, alongside the existing `DIGEST_BANNER`.

### Notes

The README's preflight sample is regenerated from a live run; the test that
pins that sample to real output caught the drift.

## [0.1.2] — 2026-07-25

A five-lens adversarial review fleet ran twice over this codebase; 27 of its
findings were independently confirmed and fixed, each with a regression test
verified by reintroducing the bug. The recurring shape, again: every unit test
passed, every event recorded, and the feature was inert or wrong in a real run.

### Fixed — trust ladder

**Semantic memory could never pass `observed`.** No projection path ever
attached verification-grade evidence to a fact, so the one category that exists
to state current truth could never state any: `presentable_as_current_truth`
was false for every fact a real run produced. Semantic promotion now accepts
the record's own landed integration or a human decision as evidence, which is
exactly what ADR-0004 always said promotes the category.

**An early self-approval permanently blocked promotion.** `_apply_review_state`
short-circuited on matching state, so a later *independent* approval was never
added to the evidence and the self-review refusal held forever — a
promotion-denial primitive an agent could trigger on its own work. The evidence
merge now always runs.

**Cross-run resolution could be overwritten by an unrelated success**, and the
`resolved_by` link was written backwards (or not at all), leaving
`Provenance.resolves_gotcha_id` permanently empty. Guarded, linked, and the
direction now matches the field's documentation.

**Review approvals never matched lessons.** The lookup keyed on the subject
alone while the writer keyed on subject-plus-finding, so the module whose stated
purpose is "this was rejected, and here is what satisfied the reviewer" was
inert in every realistic case.

**Performance aggregates published a phantom `general` bucket** with a 0.0
success rate for profiles whose real record was perfect, and dropped
reviewer-only profiles entirely.

### Fixed — boundaries

**MCP rate limiting ran after journalling**, so an untrusted client could write
unbounded refusal events; refusal bursts are now summarised into one event, and
a whole-message byte cap rejects oversized lines before parsing. A crafted
deeply-nested JSON line no longer kills the server loop.

**Typed MCP queries returned empty while matching records existed** — the type
filter ran after the limit. **Imports** now report foreign-project collisions
as issues instead of silently treating them as duplicates, say plainly that
memory/transition records are derived locally rather than counting them as
accepted, and can actually verify signatures: `provalume import` gained
`--hmac-key`, `--ed25519-key`, and `--require-signature` — the signature
subsystem existed but was unreachable from the shipped CLI.

**The PEM redaction rule was quadratic** on repeated unterminated BEGIN
markers; bounded. **No single poisoning family could reach the default
quarantine threshold**; the strongest (instruction-override) now can.

### Fixed — retrieval

**The preflight overlap tier matched raw substrings**, so a subsystem like
`test` matched every gotcha mentioning `pytest`; matching is now on word
boundaries. **The digest footer reserve was smaller than the footer**, silently
truncating rendered digests; and `omitted_count` no longer blames the budget
for near-duplicate suppression. **The browse path applied memory types as a
hard filter**, contradicting the documented nudge-not-filter rule.

### Changed — claims narrowed to what ships

Tier-1 exact-signature matching (and therefore blocking), vector retrieval, and
`as_of` validity evaluation are documented as not yet reachable from the
shipped surfaces, instead of implied working. The README's preflight sample is
now the verbatim output of a real run. LIFECYCLE's worked example no longer
claims an approval alone climbs a rung.

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
