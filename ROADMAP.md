# Roadmap

No dates. This is a solo-maintained project, and the ordering below reflects what
would most improve it rather than a schedule.

The single most useful input right now is **which limitations actually bite in
practice**. 0.1.0 ships without that data, which is why it is the first item.

---

## Next — closing the evidence gap

**Requirements mining from real runs.** Query real orchestrator event and ledger
data to rank which memory failures actually recur: repeated failed fixes,
rediscovered environment gotchas, re-proposed rejected alternatives. This is what
turns the schema and the ranking weights from reasoned to measured, and it is the
largest known weakness of 0.1.0
([`LIMITATIONS.md`](docs/reference/LIMITATIONS.md) §1).

**Dogfooding through the Orkestra integration.** The draft PR exists; running it
against real work is what produces the data above.

**Retrieval quality measurement.** A corpus large enough for a
lexical-versus-hybrid comparison to mean anything. Until then, scenario 20 tests
the plumbing and says so.

## Likely — the gaps users will hit first

**Global cross-project memory** ([ADR-0016](docs/adr/ADR-0016-global-memory-deferral.md)).
The most-requested missing feature and the riskiest to build: cross-project
leakage is the one Critical-rated confidentiality threat. The intended shape is
already recorded — a separate `~/.provalume/global.db`, per-record human
approval, stricter redaction, distinct labelling in digests. It ships when that
can be done carefully, not sooner.

**Content-level equivalence for rebased and cherry-picked commits.** Today these
degrade to `uncertain`, which is honest and, for teams that rebase constantly,
frequent.

**Incremental export.** Full serialisation is fine at current sizes and will not
stay fine.

**More integrations.** A generic adapter exists; each additional one needs
structured events rather than prose scraping, which is work on the integrator's
side as much as here.

## Considered — needs a reason, not just enthusiasm

**LLM-optional idle-time distillation.** Would produce *additional* summary
records, clearly marked as model-derived and capped at `observed`, never on the
canonical path. The test for whether it is correctly optional: removing it must
degrade compactness, never correctness.

**Richer graph retrieval.** k-NN and entity-overlap edges with 1–2-hop spreading
activation at read time. Cheap to add; unproven that it helps here.

**A web dashboard** for browsing provenance chains. The CLI's `explain` already
answers the question; a UI would answer it more pleasantly.

**Team synchronisation.** JSONL merge semantics are designed for it. Automating
it needs conflict resolution that cannot be done safely today — auto-resolving a
semantic contradiction is auto-deciding which of two contributors was right.

## Not planned

- **Conversational memory.** A different product.
- **A hosted service.** Local-first is the point.
- **Telemetry.** Not now, not later.
- **Requiring a language model** for anything canonical.
- **Multi-user ACLs.** Use filesystem permissions; a half-built ACL is worse than
  none.
- **Becoming a generic memory framework.** That field is full, and the
  differentiator here is exactly the narrowness.

## Influencing this

Open an issue naming the failure a feature would have prevented. That is more
useful than a feature request, and it is the kind of evidence this project is
built to take seriously.
