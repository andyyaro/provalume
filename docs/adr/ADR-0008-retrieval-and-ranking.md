# ADR-0008: Retrieval and ranking policy

**Status:** Accepted · **Date:** 2026-07-25

## Context

The default installation must retrieve well without embeddings. The literature
supports this: LongMemEval-V2's leading system is a file-based memory controller,
and Microsoft's LazyGraphRAG work conceded GraphRAG's indexing costs. Simple and
lexical is a defensible default, not a compromise.

A ranking function full of unexplained magic numbers is not defensible, though. It
cannot be tuned, tested, or trusted, and "why did this rank first?" becomes
unanswerable — which for Provalume is a product failure, since explainability is a
stated differentiator.

## Decision

**FTS5/BM25 plus a documented, configurable, additive scoring policy. Every
component is recorded per result and reportable.**

### The formula

```
score = w_lex           · lexical
      + w_trust         · trust
      + w_evidence      · evidence
      + w_recency       · recency
      + w_usage         · usage
      + w_type          · type_match
      + w_scope         · scope_specificity
      − p_contradiction · contradiction
      − p_poison        · poisoning_risk
```

Every component is normalised to `[0, 1]`. The score itself is **not** normalised —
its maximum is the sum of the positive weights (2.70 at defaults). Scores are
comparable within one query, not across queries. Said explicitly because a
`0.0–1.0`-looking number invites misreading as a probability.

### Components

| Component | Definition |
|---|---|
| `lexical` | FTS5 `bm25()`, sign-flipped (BM25 returns negative, lower is better) and min-max normalised across the candidate set. `1.0` when only one candidate matched. |
| `trust` | `TRUST_RANK / 5` → quarantined 0.2, observed 0.4, verified 0.6, reviewed 0.8, integrated 1.0 |
| `evidence` | `0.4` if `verification_state ∈ {passed, failed}` + `0.3` if `review_state = approved` + `0.3` if `integration_state ∈ {integrated_run, accepted_user}`, capped at 1.0 |
| `recency` | `0.5 ** (age_days / half_life_days)` — exponential decay, per-type half-life |
| `usage` | `log1p(access_count) / log1p(usage_saturation)`, capped at 1.0, `usage_saturation = 50` |
| `type_match` | `1.0` if the record's type was requested, `0.5` otherwise; `1.0` for all when no types were requested |
| `scope_specificity` | exact branch match 1.0 · repository 0.8 · project 0.6 · cross-scope 0.3 |
| `contradiction` | `1.0` if an unresolved contradiction exists, else `0.0` |
| `poisoning_risk` | the stored risk score, already `[0, 1]` |

`evidence` counts `verification_state = failed` as evidence *present*. That is
deliberate: for a gotcha, the failure **is** the evidence. Treating `failed` as
absence of evidence would systematically demote exactly the records the preflight
gate needs.

### Default weights, and why each

| Weight | Default | Reasoning |
|---|---|---|
| `w_lex` | 1.00 | Relevance dominates. A trusted fact about the wrong subsystem is noise. |
| `w_trust` | 0.50 | Substantial, but cannot outrank relevance. Half of lexical, so a strongly-matching `verified` record beats a weakly-matching `integrated` one. |
| `w_evidence` | 0.30 | Rewards evidence beyond what the trust rung already captures. |
| `w_recency` | 0.25 | Matters, but a two-year-old verified procedure is often still correct. |
| `w_usage` | 0.15 | Weak deliberately — usage is a popularity signal and self-reinforcing. |
| `w_type` | 0.20 | A nudge, not a filter. Requesting gotchas should not hide a decisive semantic fact. |
| `w_scope` | 0.30 | Local relevance is real signal. |
| `p_contradiction` | 0.40 | Demote, do not hide. The user should see contested facts, marked. |
| `p_poison` | 0.60 | The strongest penalty. Above the admission threshold a record is quarantined anyway; this handles residual risk below it. |

Per-type recency half-lives (days):

| Type | Half-life | Reasoning |
|---|---|---|
| `episodic` | 14 | What happened last sprint fades fast. |
| `performance` | 30 | Agent capability shifts with model updates. |
| `semantic` | 60 | Project facts churn, but not weekly. |
| `gotcha` | 90 | A trap that bit once tends to still be a trap. |
| `procedural` | 180 | A verified command stays verified until something changes it. |
| `decision` | 365 | An architectural decision from a year ago is still the decision. |

**These defaults are a starting position, not a measured optimum.** They encode
stated reasoning; they were not fitted to data, because no production corpus exists
yet ([`RESEARCH_VALIDATION.md`](../research/RESEARCH_VALIDATION.md) §1). Every one
is overridable in `RankingPolicy`, and the eval harness replays a fixed corpus so a
change's effect on precision and coverage is measurable rather than argued.

### Hard filters, applied before scoring

Scoring only reorders an already-authorised set. Filters are not weights:

- `project_id` must match — always, no exceptions (threat T9).
- Terminal states excluded unless explicitly requested.
- `min_trust` rank floor (default `observed`).
- Scope applicability.
- Commit validity ([ADR-0006](ADR-0006-branch-and-commit-semantics.md)).
- `invalid_at` in the past → excluded from current-truth results.
- Excluded scopes honoured.
- Candidate-set cap (default 500) before scoring, to bound work.

### Determinism

Final ordering is `(−score, −recorded_at, memory_id)`. Fully deterministic: the same
database and query produce the same order, every time. Required for the eval harness
to mean anything, and floating-point ties are broken by data rather than by dict
iteration order.

### Explainability

Every result carries a structured explanation: which filters it passed, each score
component and its contribution, and human-readable reasons — same project, same
subsystem, current at this commit, verified by command X, approved by reviewer Y,
linked to a prior failure, used successfully in later runs.

`provalume explain <memory-id>` shows the full breakdown. **If a result cannot
explain itself, that is a bug**, not a cosmetic gap.

### FTS query safety

User text is tokenised and rebuilt as double-quoted terms joined by `OR`. FTS5
operators, column filters, and prefix wildcards from user input are **stripped, not
escaped** — escaping invites a bypass, stripping does not. Term count and length are
capped. Adversarial inputs are tested (threat T22).

The cost: users cannot write FTS queries. Accepted — a memory system's query
language should not be a SQL-injection-shaped surface.

## Consequences

**Good.** Works with three pure-Python dependencies. Every ranking decision is
inspectable. The policy is data, so tuning does not mean editing scoring code.
Deterministic ordering makes evaluation reproducible.

**Bad.** Lexical retrieval misses synonyms — a query for "dependency resolution
failure" will not match a record phrased "package solver conflict". This is the real
cost of no-embeddings-by-default, and it is what optional vectors
([ADR-0013](ADR-0013-optional-vector-retrieval.md)) exist to address.

**Bad.** Nine weights and six half-lives is a lot of surface. Mitigated by defaults
that work and by the eval harness making changes measurable.

**Also bad.** Additive linear scoring cannot express interactions — "recency matters
more for episodic than procedural" is handled by per-type half-lives, but "trust
matters more when the query is about security" is not expressible. Simplicity and
explainability are worth more than expressiveness here.

## Alternatives rejected

**Vector-only or vector-first retrieval.** Makes embeddings a required dependency
and the authorisation gate, which is threat T6. Vectors reorder; they never
authorise.

**Learned ranking.** No training data, and a learned model would destroy
explainability — the thing being differentiated on.

**Multiplicative scoring** (`bm25 × decay × log(usage)`). Any zero factor
annihilates the score, so a brand-new record with zero accesses vanishes.
Additive degrades gracefully.

**Undocumented tuned constants.** The category norm, and unfalsifiable.
