# Retrieval and ranking

Every constant is here. If a number appears in the scoring code and not in this
document, that is a bug.

The decision record with the reasoning is
[ADR-0008](../adr/ADR-0008-retrieval-and-ranking.md).

---

## Filters authorise, scoring reorders

```
candidates ──▶ HARD FILTERS ──▶ authorised set ──▶ SCORING ──▶ ranked results
```

Nothing scored can appear that filtering did not admit, and no score can promote a
record past a filter. This ordering is a security property, not an optimisation:
it is what makes a poisoned vector index survivable.

### Hard filters, in order

| Filter | Behaviour |
|---|---|
| `project_id` | Must match. **No bypass, anywhere.** Cross-project leakage is the one Critical-rated confidentiality threat. |
| Terminal states | `invalidated`, `superseded`, `rejected` excluded unless `include_terminal` |
| `min_trust` | Ladder-rank floor. Default `observed`. |
| Validity | `invalid_at` set → excluded from current-truth results |
| Scope | Branch and repository applicability |
| Commit validity | Ancestry at the queried commit ([ADR-0006](../adr/ADR-0006-branch-and-commit-semantics.md)) |
| Excluded scopes | Honoured if given |
| Candidate cap | 500 candidates scored per query, bounding work |

Memory **type** is deliberately *not* a hard filter. Requesting gotchas reduces
other types' weight to 0.5; it does not hide a decisive semantic fact. This holds
on both paths: a search (query text, FTS candidates) and a browse (no query text,
structured candidates) apply the same nudge, so the parameter means one thing.

`as_of` moves **recency** only. Validity is not re-evaluated against it: a record
withdrawn after `as_of` is still withdrawn, because `invalid_at` closes a record
outright rather than at a queryable moment. Use `include_terminal` to see
withdrawn records.

## The formula

```
score = w_lex           * lexical
      + w_trust         * trust
      + w_evidence      * evidence
      + w_recency       * recency
      + w_usage         * usage
      + w_type          * type_match
      + w_scope         * scope_specificity
      - p_contradiction * contradiction
      - p_poison        * poisoning_risk
```

Every component is normalised to `[0, 1]`. **The score is not.** Its maximum is
the sum of the positive weights — 2.70 at defaults — and scores are comparable
*within one query only*. A 0-to-1-looking number would invite being read as a
probability, which it is not.

## Components

| Component | Definition |
|---|---|
| `lexical` | FTS5 `bm25()`, sign-flipped and min-max normalised across the candidate set. `1.0` when only one candidate matched. |
| `trust` | `rank / 5`: quarantined 0.2, observed 0.4, verified 0.6, reviewed 0.8, integrated 1.0. Terminal states 0. |
| `evidence` | `0.4` if verification ran + `0.3` if review approved + `0.3` if landed, capped at 1.0 |
| `recency` | `0.5 ** (age_days / half_life_days)` |
| `usage` | `log1p(access_count) / log1p(50)`, capped at 1.0 |
| `type_match` | `1.0` if the type was requested, `0.5` otherwise; `1.0` when none requested |
| `scope_specificity` | branch 1.0 · repository 0.8 · project 0.6 · cross-scope 0.3 |
| `contradiction` | `1.0` if an unresolved contradiction exists |
| `poisoning_risk` | the stored `[0, 1]` risk score |

### `verification_state = failed` counts as evidence *present*

Deliberate, and easy to get backwards. For a gotcha, the failure **is** the
evidence. Treating `failed` as absence of evidence would systematically demote
exactly the records the preflight gate needs. Only `unknown` scores zero.

## Weights

| Weight | Default | Reasoning |
|---|---:|---|
| `w_lex` | 1.00 | Relevance dominates. A trusted fact about the wrong subsystem is noise. |
| `w_trust` | 0.50 | Half of lexical, so a strongly-matching `verified` record beats a weakly-matching `integrated` one. |
| `w_evidence` | 0.30 | Rewards evidence beyond what the trust rung already captures. |
| `w_recency` | 0.25 | Matters, but a two-year-old verified procedure is often still correct. |
| `w_usage` | 0.15 | Weak deliberately — usage is self-reinforcing and would otherwise ossify the ranking. |
| `w_type` | 0.20 | A nudge, not a filter. |
| `w_scope` | 0.30 | Local relevance is real signal. |
| `p_contradiction` | 0.40 | Demote, do not hide. A contested fact should be visible and marked. |
| `p_poison` | 0.60 | Strongest penalty. Above the admission threshold a record is quarantined anyway. |

### Recency half-lives (days)

| Type | Half-life | Reasoning |
|---|---:|---|
| `episodic` | 14 | Last sprint's episode fades fast |
| `performance` | 30 | Agent capability shifts with model updates |
| `semantic` | 60 | Project facts churn, but not weekly |
| `gotcha` | 90 | A trap that bit once tends to still be a trap |
| `procedural` | 180 | A verified command stays verified until something changes it |
| `decision` | 365 | An architectural decision from a year ago is still the decision |

### Shape parameters

| Parameter | Default |
|---|---:|
| `usage_saturation` | 50 |
| `type_mismatch_factor` | 0.5 |
| `candidate_cap` | 500 |
| `poisoning_quarantine_threshold` | 0.5 |
| `rrf_k` | 60 (Cormack et al., 2009) |

## These defaults are reasoned, not fitted

They encode stated reasoning. They were **not** tuned against a production corpus,
because none exists yet ([`LIMITATIONS.md`](LIMITATIONS.md) §1). Expect them to
move once real data arrives.

Every one is overridable:

```python
from provalume import Provalume, RankingPolicy

pv = Provalume.open(policy=RankingPolicy(w_recency=0.5, w_usage=0.0))
```

The eval harness replays a fixed corpus, so the effect of a change is measurable
rather than argued.

## Deterministic ordering

```
(−score, −recorded_at, memory_id)
```

The same database and query produce the same order, every time. Ties break on
recency, then on identifier — both data, rather than accidents of dict iteration
or floating-point comparison. Required for the eval harness to mean anything.

## Explanations

Every result carries a structured explanation: which filters it passed, each score
component and its weighted contribution, human-readable reasons, and warnings.

```sh
provalume recall "integration tests" --explain
provalume explain <memory-id> --transitions
```

`--explain` prints the reasons and warnings. The component table below is carried
on every result as `result.explanation.breakdown`; `provalume demo` renders it.

```
why: matched the query text (relevance 1.00)
why: same project (my-app)
why: recorded on this branch (main)
why: current here — a1b2c3d4e5f6 is an ancestor of f9e8d7c6b5a4
why: failed `pytest -n auto tests/integration`; seen 2 times
why: linked to what later worked

lexical         1.000 x weight = +1.000
trust           0.600 x weight = +0.300
evidence        0.400 x weight = +0.120
recency         1.000 x weight = +0.250
type_match      1.000 x weight = +0.200
scope           1.000 x weight = +0.300
TOTAL           2.170
```

Rows whose component and contribution are both zero are not printed — `usage` is
absent above because this record had not been retrieved before. Every non-zero
row is shown, so the arithmetic closes.

**A result that cannot explain itself is a bug**, not a cosmetic gap. The
components always sum to the total; a test asserts it.

## Query safety

User text never reaches FTS5 as query syntax. It is tokenised and rebuilt as
double-quoted terms, and FTS5 operators, column filters, and prefix wildcards are
**stripped rather than escaped** — escaping invites a bypass, stripping does not.

Terms preserve the punctuation that appears inside real identifiers, because this
corpus is mostly commands and paths: `src/main.py:42`, `pytest-xdist`,
`no:xdist`, and `c++` all survive as single terms.

Bounds: 32 terms, 64 characters per term, 4096 characters per query. Over-long
queries are truncated rather than rejected — a long query is a paste, not an
attack.

The cost: you cannot write FTS queries. No `NEAR`, no boolean operators, no column
filters. Accepted, because a memory system's query language should not be an
injection-shaped surface.

## The digest

```python
digest = pv.recall("integration tests").digest(char_budget=2000)
```

- **The banner is always first and always present.** Fixed wording — it is the
  control for instruction replay.
- **The budget is a hard ceiling enforced by construction.** Items are measured
  before inclusion — against the footer that will actually be rendered, not a
  fixed guess at its size — so the digest is never assembled and then trimmed. A
  post-hoc trim can cut mid-item and leave a claim without its trust label, and
  the first thing it cuts is the warnings line.
- Failures are ordered first. An agent about to repeat a mistake needs that before
  general facts.
- Near-duplicates are suppressed on `(type, text)`.
- Omitted records are counted and reported. `omitted_count` means *budget
  overflow only* — records a larger budget would have admitted. Near-duplicates
  are reported separately as `suppressed_duplicates`, because raising the budget
  will not bring those back.
- A token budget converts at 4 characters per token — a documented **estimate**,
  because the true ratio is model-specific. Pass a character budget for exactness.

## Optional vectors

**Not wired in 0.1.x.** `RecallQuery.use_vectors` is accepted and ignored:
`RetrievalEngine.recall()` does not read it, and no SDK, CLI or MCP path writes a
vector, so `memory_vectors` stays empty and every retrieval is lexical. Setting
the flag changes nothing.

What ships is the machinery — `VectorIndex`, `HashingEmbedder` and
`reciprocal_rank_fusion` in `provalume.retrieval.vectors` — exercised directly by
eval scenario 20 and by `provalume doctor`, not by the read path. Scenario 20
fuses a lexical result list by hand and measures *plumbing*: that fusion runs, and
that a vector hit cannot authorise a record the filters excluded. It is not a
retrieval-quality comparison, and the baseline embedder is a hashing projection
with no semantic content.

The design the flag is reserved for is unchanged: off by default, never an
authorisation gate, vector results passing through the same filter implementation
as lexical ones, and fusion over ranks rather than scores because BM25 and cosine
live on incomparable scales.

See [ADR-0013](../adr/ADR-0013-optional-vector-retrieval.md) and
[`BENCHMARKS.md`](BENCHMARKS.md) for what is and is not measured.
