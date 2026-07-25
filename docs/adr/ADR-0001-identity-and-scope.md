# ADR-0001: Provalume identity and scope

**Status:** Accepted · **Date:** 2026-07-25

## Context

The agent-memory category is saturated. `claude-mem` has 88.5k stars and owns
local-first cross-CLI session memory. Mem0 (61.7k), Cognee (29.3k), Graphiti
(29.2k), and Letta (24.0k) own the LLM-extraction engine space. Beads (25.6k) owns
git-native agent state. Shipping a generic memory framework in 2026 means arriving
two years late with nothing to say.

The [competitor review](../research/COMPETITOR_TRIALS.md) found one thing no
released project does: **store what a deterministic process verified, with the
evidence attached.** Not one system reviewed carries
`{verification command, pass/fail, reviewer verdict, commit SHA}` as first-class
queryable provenance, and not one models whether a fact is valid at a given commit.

## Decision

**Provalume is verified memory for autonomous software engineering.** Its identity
is a single sentence:

> Facts your agents proved, not things they said.

**In scope for the project:**

- Verification-grounded promotion — trust from deterministic evidence only.
- Independent-review provenance — who approved it, and that they were not the author.
- Branch-aware and commit-aware truth.
- Deterministic writes, reproducible from the same inputs.
- Failed-attempt memory as a first-class category, not an afterthought.
- Cross-agent learning within a project.
- Explainable retrieval — every result answers "why was this returned?".
- Memory-poisoning resistance as an architectural property.

**Explicitly out of scope, permanently:**

- Conversational memory. Provalume does not remember your favourite colour and
  will not be benchmarked as if it should.
- LLM-based extraction, summarisation, or consolidation in any canonical path.
- Being a generic memory wrapper or an "agent framework".
- A hosted service, an account, or telemetry.

**Out of scope for 0.1.0 but not forever:** global cross-project memory
([ADR-0016](ADR-0016-global-memory-deferral.md)), LLM-optional idle-time
distillation, a web dashboard, multi-user access control.

**Naming.** The name deliberately avoids the exhausted `mem*` namespace and sits
in the proof/provenance semantic field. Clearance recorded in
[`NAME_CLEARANCE.md`](../research/NAME_CLEARANCE.md).

**Standalone, not extracted.** The research report recommended building inside
Orkestra and extracting after a month of dogfooding. Provalume ships standalone
from day one. This is a directed decision; the trade-off — shipping without
production-mined requirements — is accounted for in
[`RESEARCH_VALIDATION.md`](../research/RESEARCH_VALIDATION.md) §1 and listed as
the largest known weakness in [`LIMITATIONS.md`](../reference/LIMITATIONS.md).

## Consequences

**Good.** A defensible position that does not depend on out-executing a
88.5k-star incumbent. The differentiator is architectural — a competitor cannot
add verification provenance without acquiring a deterministic verification
process. The dependency direction comes out right by construction, because there
is no host to accrete couplings to.

**Bad.** Provalume is useless without something that produces verification
evidence. A user with no test suite and no review process gets an event log. This
narrows the audience substantially and is the correct trade: a system for everyone
would have nothing to prove.

**Also bad.** Refusing conversational memory means refusing the benchmarks and the
demo that make the category legible. Provalume has to explain itself.

## Alternatives rejected

**Adopt an existing engine.** Every engine reviewed puts an LLM in the write path,
which makes stored content non-reproducible — fatal for a system whose claim is
"this was proved". Several also carry dependency risk (Zep CE discontinued, Letta
flagship legacy, Kuzu archived, Graphiti CLA'd).

**Wrap claude-mem.** It would mean depending on a project whose roadmap someone
else steers, for the component that is meant to be the identity. It is also
session-compression memory with no verification concept — there would be nothing
to wrap.

**Build generic.** Memory repo #41.
