# Limitations

Read this before adopting Provalume. It is the honest list, not a marketing
"known issues" section, and the first item is the largest.

---

## 1. It has not been dogfooded on production runs

**Partly addressed.** One real orchestration run has now been driven end to end
(real git worktrees, real failing verification commands, real integration
commits) with fake agents standing in for vendor CLIs. That run found three
defects no test had caught — see `IMPLEMENTATION_TRACKER.md`, bugs 15–17. What
it still does not establish is whether a digest measurably helps a real model,
which needs real agents and real quota.


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

**Confirmed by dogfooding.** A gotcha is keyed on the command and the error, so
it is findable by `ConnectionError` or `transient upstream reset` but *not* by
`uploader retry` — the feature being worked on appears nowhere in the record. A
developer asking "what do we know about the uploader?" gets nothing, while one
who already knows the error message gets the answer. That is backwards from how
the question usually arrives.


The default installation uses FTS5 and BM25. A query for "dependency resolution
failure" will not match a record phrased "package solver conflict". This is the
real, direct cost of not requiring embeddings, and it is what the optional vector
extras exist to address.

## 14. Benchmarks are self-comparisons

The eval harness compares Provalume against Provalume, on Provalume's own
fixtures. Several metrics have small denominators — five adversarial records, three
cross-scope checks — because they are targeted scenarios rather than a large
corpus. Rates are always reported with their denominators for that reason.

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
