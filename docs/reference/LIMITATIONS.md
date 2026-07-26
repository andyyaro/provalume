# Limitations

Read this before adopting Provalume. It is the honest list, not a marketing
"known issues" section, and the first item is the largest.

---

## 1. It has not been dogfooded on production runs

**Partly addressed.** Two real orchestration runs have now been driven end to
end — the second through failure, a genuine fix, and success — with real git
worktrees, real failing verification commands, and real integration commits.
Fake agents stand in for vendor CLIs. Between them these runs found seven
defects no test, eval, or review had caught (`IMPLEMENTATION_TRACKER.md`, bugs
15–21), including that no memory could climb past `verified` in a real run and
that cross-run resolution was unreachable.

That scenario family has since been re-run against released 0.1.4 and
captured as replayable fixtures: the trajectory suite
(`provalume eval --suite trajectories`, [ADR-0019](../adr/ADR-0019-trajectory-benchmark.md))
replays the captured adapter calls and scores every decision point, so the
behaviour the dogfooding exercised stays verified on every commit instead of
once. (The bug-finding runs themselves predate the capture shim and were not
recorded; the fixtures re-create the same stories against the fixed code.)

What this still does not establish is whether a digest measurably helps a real
model. That needs real agents and real quota. Everything verified so far —
including the trajectory suite — concerns what gets *recorded* and
*retrieved*, not whether an agent reads it and does better.


**This is the biggest weakness of 0.1.0.**

Provalume's schema, its six memory categories, its promotion rules, and its
ranking weights come from the research literature, a hands-on competitor review,
and the shape of an existing orchestrator's event tables. They do **not** come
from mining real production runs to find which memory failures actually recur.

The research report that informed this project recommended building inside an
orchestrator first and dogfooding for at least a month before extracting. That
recommendation was deliberately overridden in favour of a clean dependency
boundary; the reasoning and the trade-off are in
[`RESEARCH_VALIDATION.md`](../research/RESEARCH_VALIDATION.md) §1.

**What this means in practice.** The twenty-scenario eval harness encodes the
failure modes the literature names, and they are reproducible — but they are
synthetic. Nobody has yet measured how often a real agent fleet re-runs a
known-failed command, or which of the six categories earns its keep. Expect the
ranking weights and possibly the category boundaries to move once that data
exists. The compatibility and migration machinery
([ADR-0017](../adr/ADR-0017-compatibility-and-versioning.md)) is there to absorb
it.

## 2. Retrieved memory cannot be forced to stay data

Every digest opens with a banner stating that its contents are untrusted
reference data and not instructions. Every item carries its trust state and
provenance inline. Imperative patterns raise a poisoning-risk score.

**None of that can make a language model honour the banner.** A sufficiently
well-crafted imperative sentence inside a stored memory may still be obeyed by a
future agent. Provalume's controls reduce the blast radius — keeping hostile text
out of high trust tiers, scoping it to where it came from, bounding the digest —
but the residual risk is real and unsolved.

Provalume's guarantee is about **provenance and labelling**, not about model
obedience. See [`MEMORY_POISONING.md`](../security/MEMORY_POISONING.md) §2.4.

## 3. Tamper resistance is detection, not prevention

Events are append-only, enforced by database triggers, and each carries a hash
chained to its predecessor. `provalume audit` recomputes the chain and reports
the first divergence.

An attacker who can write to `.provalume/provalume.db` can edit it and recompute
the chain. What they cannot do is edit it *invisibly* unless they also control
wherever the chain head was recorded. Provalume exposes the head so you can pin
it externally; it does not pin it for you.

The same applies to rollback: replacing the database with an older copy is
detectable only if you kept a record of the previous head.

## 4. No access control, no multi-user isolation

There is no authentication, no authorisation, no per-user permissions, and no
notion of a memory another user may not read. Anyone who can read the database
file reads everything.

Do not place a Provalume database on a shared host and expect isolation between
users. Use filesystem permissions and disk encryption. The MCP server's
`--read-only` mode limits what a *client* can do; it is not a substitute.

## 5. No hard deletion

The journal is append-only by design. `invalidate` and `supersede` withdraw a
record without removing it, because a provenance system that can quietly delete
its own history is not a provenance system.

**Provalume is a poor fit for data under a deletion requirement.** If a secret or
a personal datum enters the journal, the reliable remedies are to delete the whole
database, or to export with filters and re-import into a fresh one. There is no
surgical redaction of an already-written event.

## 6. Redaction catches known patterns only

Redaction runs before every durable write and covers provider-prefixed keys,
structured credential fields, JWTs, PEM blocks, URL userinfo, and generic
credential assignments. `provalume audit` re-scans stored content.

A credential with **no recognisable shape** — a bare password, an internal API key
with no prefix — can survive both. A clean audit proves that no *known pattern*
matched; it is not clearance. If you know a specific secret transited a run,
treat the database as containing it and rotate.

Redaction also over-fires: it favours recall over precision, so you will
occasionally see `[REDACTED]` where nothing was secret.

## 7. Rebase and cherry-pick degrade to "uncertain"

Commit validity is evaluated by Git ancestry. After a rebase the original commit
SHAs are unreachable; after a cherry-pick the same change has a different SHA. In
both cases Provalume detects that it *cannot tell* and labels applicability
`uncertain` rather than guessing.

For a team that rebases constantly this will read as a regression — memory
becomes less confident exactly where history was rewritten. The alternative is a
confident wrong answer, which is worse. Content-level equivalence detection is
roadmap work.

Without a Git repository at all, commit validity is unavailable entirely and
everything falls back to scope-only filtering.

## 8. Contradiction detection is literal

Two current semantic records are flagged as contradicting only when they share a
normalised **subject key** — lowercase, punctuation stripped, stopwords removed.

It will miss paraphrases. "We use uv" and "the package manager is uv" produce
different keys and will not be compared. Detecting more would mean interpreting
text, which means a language model in the read path, which this design refuses.
A missed contradiction costs a warning; a fabricated one would demote a correct
fact.

## 9. Failure-signature matching is heuristic

Signatures normalise away paths, timestamps, PIDs, durations, and hashes so that
the same failure matches itself across runs. That normalisation is lossy in both
directions: distinct failures can collide, and the same failure phrased
differently can fail to match.

Every normalisation rule is individually tested and the false-positive rate is an
eval metric rather than an assumption — but two failures sharing a signature are
*probably* the same failure, never certainly.

## 9a. A resolution is repository-scoped, not task-scoped

A failure signature is keyed on the command and the error, not on the task that
hit it. Under an orchestrator the verification gate is repo-wide, so two
unrelated tasks share one signature — and when either one's work lands and the
command passes, the signature is marked resolved while the other task may still
be blocked.

This is deliberate as far as it goes: the signature says "this command failed
this way in this repository", and a landing that makes the command pass is a
real answer to that. What it does *not* say is "your task is fixed". Only work
that actually landed can resolve anything — a pass inside a worktree that is
later discarded (merge conflict, rejected review, exhausted budget, or a
non-mutating task) never resolves a failure, which is enforced and tested.

The residual: a still-blocked task can find its shared signature already
resolved by a sibling's landing, and the gate will describe that landing rather
than warn afresh.

## 9b. Rebuild regenerates transition identifiers and timestamps

Memory rows rebuild byte-identically from the journal. Transition rows do
not: each rebuild mints fresh `transition_id`s and stamps `recorded_at` with
the rebuild's own wall clock. The semantic audit trail — which states, under
which rule, on which evidence, by which actor — is journal-derived and
survives exactly; *when the transition originally happened* does not survive
a rebuild. If original transition times matter to you, they are recoverable
from the evidence events' timestamps in the journal, not from the transitions
table of a rebuilt database. (Found while pinning rebuild determinism for the
freshness axis; the freshness events did not cause it.)

## 9c. Blast-radius extraction is bounded, and honest about it

A blast radius (ADR-0020) can only ever bound what its method can see:

- **`import_graph` is static.** Dynamic imports (`importlib`,
  `__import__`, plugin registries, entry points) are invisible to an AST
  walk. Source roots are the repository root and `src/`; a bespoke layout
  configured through build tooling is not probed. A bare `pytest` with no
  path arguments names no entry, so it falls through to `commit_touch`.
- **`coverage` measures the parent process.** A verification whose real work
  happens in spawned subprocesses may yield a radius covering only the
  parent (measuring children needs `COVERAGE_PROCESS_START` cooperation the
  extractor does not impose). The project's own coverage configuration is
  deliberately neutralised — a radius describes what ran, not what the
  project chose to report on.
- **`commit_touch` is proximity, not causality** — the files that changed
  alongside the evidence. It is recorded as the weakest method for a reason.
- **A git-less client records no radius at all** and its records stay
  `unverifiable` on the freshness axis; the in-memory, git-free clients this
  project's own test suite mostly uses are the designed example.

In every case the failure direction is chosen deliberately: a radius that
cannot be computed honestly is not recorded (the record stays
`unverifiable`), and a radius is never truncated to fit — over the cap, the
method fails and a weaker one gets its turn.

## 9d. Freshness scanning is explicit, and `current` means "no scanned landing"

There is no daemon and no background watcher, deliberately (ADR-0020, T29).
Freshness triggers fire only when `provalume freshness <sha>` runs — from a
post-merge hook or by hand — for a commit the operator asserts has landed.
A landing nobody scanned is invisible: a record can read `current` while an
unscanned commit rewrote everything underneath it. `current` is therefore a
claim about the landings Provalume was shown, never about the repository in
general. If the hook is not installed, the axis degrades to exactly what
existed before it: nothing, honestly labelled.

## 10. Single writer, single machine

Provalume assumes one writing process. Concurrent writers serialise on SQLite's
busy timeout rather than failing, but the design is single-writer and the tests
verify that discipline rather than true concurrency.

There is no networked or shared database. Team collaboration goes through JSONL
export and import, which is manual, and which cannot fully reconstruct trust —
trust is re-derived locally from evidence, so two teammates importing each
other's exports may legitimately reach different trust states for the same
record.

## 11. No cross-project or global memory

Deliberate ([ADR-0016](../adr/ADR-0016-global-memory-deferral.md)). Machine-level
knowledge — "this laptop needs `colima start` first" — is rediscovered in every
project. Cross-project leakage is the one Critical-rated confidentiality threat
in the model, and the safest 0.1.0 answer is that the capability does not exist.

This is the limitation users are most likely to hit first.

## 12. Vector retrieval is experimental and unmeasured

Vector retrieval is optional, off by default, and marked experimental. The
built-in `HashingEmbedder` is a **non-semantic test baseline** that exists so the
vector code path is exercised in CI without an optional dependency — never treat
its scores as a retrieval-quality result.

No comparison of lexical against hybrid retrieval has been run on a corpus large
enough to mean anything. Eval scenario 20 verifies the *plumbing* — that fusion
runs and that vectors cannot bypass governance — not the quality.

## 13. Lexical retrieval misses synonyms

**Confirmed by dogfooding, and now pinned as fixtures.** A gotcha is keyed on
the command and the error, so it is findable by `ConnectionError` or
`transient upstream reset` but *not* by `uploader retry` — the feature being
worked on appears nowhere in the record. A developer asking "what do we know
about the uploader?" gets nothing, while one who already knows the error
message gets the answer. That is backwards from how the question usually
arrives. The trajectory suite carries `known_limitation` probes that pass by
missing and fail if the behaviour ever silently changes — and the same gap
operates at the integration's brief-digest choke point, which queries by task
title (see `BENCHMARKS.md`, "What the trajectories showed").


The default installation uses FTS5 and BM25. A query for "dependency resolution
failure" will not match a record phrased "package solver conflict". This is the
real, direct cost of not requiring embeddings, and it is what the optional vector
extras exist to address.

## 14. Benchmarks are self-comparisons

Both suites compare Provalume against Provalume — the twenty scenarios on
fixtures this project wrote, the trajectory suite on call logs this project
captured from its own dogfood runs (one scripted project family, placeholder
agents, one machine). Several metrics have small denominators — five
adversarial records, nineteen decision points — because they are targeted
scenarios and four trajectories rather than a large corpus. Rates are always
reported with their denominators for that reason.

**No LongMemEval-V2 score is claimed**, no comparison against another system is
made, and no superiority claim appears anywhere in this repository. See
[`BENCHMARKS.md`](BENCHMARKS.md).

## 15. Vendor context-file behaviour is unverified

Worktree materialization writes `AGENTS.md` / `CLAUDE.md` / `GEMINI.md` and
removes them deterministically before staging. What is **tested** is the
write-and-cleanup contract.

What is **not** tested is whether any particular vendor CLI actually reads those
files under the conditions Provalume writes them. The research report flagged two
such facts as resting on secondary sources; neither was verified, and Provalume's
primary path — the prompt splice — depends on neither. See
[ADR-0015](../adr/ADR-0015-worktree-materialization.md).

## 16. The Orkestra integration is not production-proven

The adapter is tested against fixtures and the integration branch runs Orkestra's
own test suite. It has not run against production traffic, and it ships as a
**draft** pull request rather than a merged, released integration.

---

## What Provalume does not try to be

- A conversational memory system.
- A generic agent framework.
- A hosted service.
- Useful without something that produces verification evidence. If your project
  has no tests and no review process, Provalume gives you an event log with
  provenance and little else.

If any limitation above is a blocker for you, say so in an issue — several are
tracked as roadmap items in [`ROADMAP.md`](../../ROADMAP.md), and knowing which
ones bite in practice is exactly the data 0.1.0 lacks.
