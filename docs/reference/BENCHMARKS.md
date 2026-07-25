# Benchmark methodology

**No comparison against any other system appears in this repository, and no
superiority claim is made anywhere.** Every figure below is Provalume measured
against Provalume, on Provalume's own fixtures.

That is a deliberate position, not modesty. This field has a documented record of
headline numbers being corrected downward after publication, and a benchmark you
cannot reproduce is a marketing asset rather than evidence.

---

## What is measured

A replayable harness of **twenty scenarios**. Each builds a fresh in-memory
database, drives the **real** engine — same storage, same policy, same
retrieval, same projections — and asserts on what comes out. Nothing is mocked
and nothing is estimated.

```sh
provalume eval                      # run all twenty
provalume eval --scenario poisoning # run one
provalume eval --json --out results.json
```

Scenarios are deterministic: they pass explicit timestamps wherever recency
matters, so a run today and a run next year produce the same result.

| # | Scenario | What it proves |
|---|---|---|
| 1 | `repeated-failed-fix` | A repeat of a known-failed fix produces a warning |
| 2 | `environment-gotcha` | An environment gotcha is recalled later by description |
| 3 | `decision-recall` | A decision and its rejected alternatives survive |
| 4 | `stale-fact` | A superseded fact is not served as current truth |
| 5 | `branch-isolation` | A branch-local fact does not leak to another branch |
| 6 | `contradictory-worktrees` | Concurrent branches may disagree without corruption |
| 7 | `rejected-not-truth` | Rejected-branch knowledge never becomes project truth |
| 8 | `procedure-promotion` | A procedure climbs the full ladder, rung by rung |
| 9 | `human-decision` | A human decision carries project authority |
| 10 | `sensitive-isolation` | Secrets are redacted before the durable write |
| 11 | `poisoning` | Adversarial records never exceed `observed` |
| 12 | `context-budget` | The digest budget is a hard ceiling |
| 13 | `rebuild` | Projections rebuild identically from the journal |
| 14 | `jsonl-merge` | Export is deterministic; import refuses forgery and conflict |
| 15 | `cross-project` | A query never returns another project's records |
| 16 | `historical-recall` | Withdrawn records remain retrievable as history |
| 17 | `agent-performance` | Agent outcomes aggregate into performance memory |
| 18 | `reviewer-finding` | A recurring reviewer finding is retrievable |
| 19 | `false-positives` | Unrelated actions do not trigger warnings |
| 20 | `lexical-vs-hybrid` | Hybrid retrieval runs and cannot bypass governance |

## Committed baseline

Recorded on 2026-07-25 from a full run, committed at
[`evals/results/baseline/results.json`](../../evals/results/baseline/results.json).

**20/20 scenarios passed.**

| Metric | Result | Target |
|---|---|---|
| Poisoning success rate | **0/5 (0%)** | **0** — a non-zero result is a bug, not a tuning parameter |
| Cross-scope leakage | **0/3 (0%)** | 0 |
| Stale-memory rate | **0/1 (0%)** | 0 |
| False warnings | **0/5 (0%)** | as low as possible |
| Recall precision | 2/2 (100%) | higher is better |
| Recall coverage | 1/1 (100%) | higher is better |
| Procedure reuse | 1/1 (100%) | higher is better |
| Repeated-error rate | 0/1 (0%) | lower is better |
| Retrieval latency | p95 **1.7 ms** | — |
| Write latency | see results file | — |
| Rebuild latency | see results file | — |

### Read the denominators

Every rate above is reported with its denominator, and several are small — five
adversarial records, three cross-scope checks, one stale-fact check. These are
**targeted scenarios, not a corpus.** "0% poisoning success over five adversarial
records" is a meaningful regression guard and a weak statistical claim. It is
reported this way so nobody mistakes one for the other.

Metrics whose scenarios did not run report a zero denominator rather than a
fabricated zero rate, so an unrun measurement is visibly unrun rather than
looking like a perfect score. `task_completion`, `verification_improvement`, and
`review_cycle_reduction` currently read `n/a (0 observations)` — they need
production trajectories that 0.1.0 does not have.

## What is deliberately not used

**LoCoMo is not used**, as a headline or otherwise. Its contexts are small, it is
gameable, and both major vendors' headline numbers on it were corrected downward
after a third-party audit found problems including corrupted answer keys.

**No LongMemEval-V2 score is claimed.** LongMemEval-V2 is the right benchmark for
this space — agent trajectories at real scale — and Provalume 0.1.0 does not run
it. What ships is a LongMemEval-V2-*style* harness over software-agent task
trajectories: own scenarios, own fixtures, own metrics, committed and
re-runnable. Saying "LongMemEval-V2-style" is a description of the *shape*, not a
claim about the *score*.

**Conversational recall is absent.** The favourite-colour genre measures nothing
Provalume claims to do.

## The lexical-versus-hybrid question, honestly

Scenario 20 is often the first one people look at. It does **not** measure
retrieval quality.

The built-in `HashingEmbedder` is a hashing-trick projection with no semantic
content: it captures exact token overlap and nothing else. It exists so the
vector code path — fusion, fallback, rebuild, and the guarantee that vectors
cannot bypass governance — is exercised by CI on every commit without requiring
an optional dependency.

So scenario 20 proves the *plumbing*: that reciprocal rank fusion runs, and that
an adversarial embedding cannot return a record governance did not authorise
(threat T6). It proves nothing about whether hybrid retrieval finds better
answers than lexical retrieval.

**A real lexical-versus-hybrid comparison needs a corpus large enough for the
difference to be measurable, and it has not been run.** When it is, it will be
published with its methodology, its corpus, and its denominators — and it will
still be Provalume against Provalume.

## Reproducing

```sh
git clone https://github.com/andyyaro/provalume
cd provalume
uv venv && uv pip install -e ".[vectors,signatures]"
uv run provalume eval --json --out /tmp/mine.json
diff <(jq -S '.scenarios[].passed' evals/results/baseline/results.json) \
     <(jq -S '.scenarios[].passed' /tmp/mine.json)
```

Latency figures will differ by machine; the pass/fail results and the governance
metrics (poisoning, leakage, staleness) should not.

## What would make these numbers meaningful

Production trajectories. Until Provalume has run against real agent fleets, these
scenarios verify that the mechanisms work as designed — which is worth having and
is not the same as knowing they matter. That gap is the first item in
[`LIMITATIONS.md`](LIMITATIONS.md).
