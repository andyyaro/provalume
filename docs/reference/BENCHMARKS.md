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

Recorded on 2026-07-26 from a full run at 0.1.4, committed at
[`evals/results/baseline/results.json`](../../evals/results/baseline/results.json).
(The file was first recorded at 0.1.0 and regenerated when the trajectory
suite added three counters; the regeneration also absorbed 0.1.0→0.1.4 drift
in the digest-size statistics. Every pass/fail result and governance rate is
unchanged.)

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
| Retrieval latency | p95 **0.8 ms** | — |
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
`review_cycle_reduction` still read `n/a (0 observations)`: the trajectory
suite below replays what real runs *recorded and retrieved*, which cannot
honestly say whether an agent *did better* — that needs A/B runs with real
agents, and a test asserts these denominators stay zero until it happens.

## The trajectory suite

The second suite replays **call logs captured from real Orkestra dogfood
runs** — real git worktrees, real failing pytest gates, real retries, a real
decision gate (its answer scripted by the capture harness), real integration
commits — through the same `OrkestraAdapter` surface the integration uses,
against a fresh database, and scores what memory owed the agent at each
captured decision point
([ADR-0019](../adr/ADR-0019-trajectory-benchmark.md),
[`evals/fixtures/trajectories/`](../../evals/fixtures/trajectories/README.md)).

```sh
provalume eval --suite trajectories                     # from a repo checkout
provalume eval --suite trajectories --scenario fix-lands
```

| Trajectory | Competencies (LongMemEval-V2 shape, not score) | Story |
|---|---|---|
| `fix-lands` | premise awareness, workflow knowledge, dynamic state | Gate fails twice, fix lands in run 2, resolution presented at every later decision point |
| `repeat-blocked` | premise awareness, dynamic state | Four failures across two runs, no fix; occurrences accumulate; the scripted abort decision is remembered |
| `env-gotcha` | environment gotchas, workflow knowledge | `ModuleNotFoundError` at collection; recallable by error text, invisible to a description query |
| `two-command-gate` | premise awareness, workflow knowledge | Two-command gate: warns about the failing command at every decision point, never about the passing one |

Recorded on 2026-07-26, committed at
[`evals/results/trajectories/results.json`](../../evals/results/trajectories/results.json).
**4/4 trajectories passed.** Each denominator states its composition —
captured in-run decision points versus authored post-state probes — because
the two are not the same kind of observation.

| Metric | Result | Reading |
|---|---|---|
| Repeated-error rate | **0/16 (0%)** | 15 captured decision points plus 1 post-state probe where the gate owed the failure's history — a warning while unresolved, or the resolution after it landed; none was silent |
| False warnings | **0/9 (0%)** | 9 captured decision points owed silence (cold starts, a command that never failed); none warned |
| Occurrence fidelity | **16/16 (100%)** | every non-silent point's occurrence count matched the trajectory's true failure count, across runs |
| Resolution surfacing | **6/6 (100%)** | every post-landing captured decision point presented the resolution and named the landing commit |
| Digest inclusion | **15/15 (100%)** | 12 captured decision points plus 3 post-state probes: the required record *content* — not just section headings — appeared in the digest text (budget compliance is a separate hard check on every digest) |
| Recall coverage / precision | 4/4, 4/4 | post-state recall probes by error text and decision question; three further known-limitation probes are excluded by design and pass by missing |

### Read the denominators (again)

Sixteen should-warn observations come from four trajectories of one scripted
project family, captured on one machine. These are regression guards over
real recorded behaviour, not a statistical claim about fleets. The suite
exists so the *next* real trajectory — from any project — can be added as a
fixture and scored the same way, at the cost the format demands: expectations
are authored by hand against the new capture's evidence.

### What the trajectories showed that synthetic scenarios did not

- **The failure knowledge travelled through the preflight channel, not the
  digest.** The brief digest is queried by task *title*. At every failing
  task's own decision point the title (`Implement the specification`) shared
  no vocabulary with the failing command or its error, so the digest carried
  agent-performance records while the warning channel carried the failure.
  One other task's title did overlap — `Add or extend tests for the
  implementation` shares "tests" with `pytest -q tests/` — and its digest
  retrieved the failure records, which is the exception that proves the
  mechanism: inclusion is lexical overlap, not relevance. That is LIMITATIONS
  §13 operating at the integration's choke point, now measured instead of
  predicted.
- **Known misses are pinned as fixtures that fail if the behaviour moves.**
  Probes marked `known_limitation` pass by missing and touch no counter:
  `uploader retry` and `dependency typo` find no gotcha under lexical
  retrieval (and in one trajectory the word "retry" finds the recorded
  *decision* instead, through its rejected option). If vector retrieval ever
  moves these, the fixtures will say so.
- **A failing trajectory mints no truth.** `repeat-blocked` asserts zero
  integrated procedures — four failures and an abort leave verified failure
  evidence, verified performance aggregates, and one integrated decision
  record, and nothing reaches integrated truth through work, because no work
  landed.

### What replay cannot show

The practice agents in the captured runs are scripted placeholders. Attempt
outcomes in the logs say nothing about memory quality and are never scored as
if they did. Whether a real model reads a digest and repeats fewer failures
remains unmeasured (LIMITATIONS §1), and the agent-outcome metrics stay at
zero denominators until it is measured honestly.

Replay is also not byte-identical to the live runs: it uses `git=None`, so
events lose the commit anchors the live client stamped and commit-anchored
digest items render `applicability: UNCERTAIN` where the live run said
`HISTORICAL` (ADR-0019, "Fidelity limits"). The suite compares every replayed
read against the captured return and reports each divergence as a note in the
results — currently three, one per landing trajectory's largest digest —
so the gap is visible rather than silent, and the CURRENT/HISTORICAL/UNCERTAIN
labelling itself is explicitly outside what this suite can regress-test.

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
uv run provalume eval --suite trajectories --json --out /tmp/traj.json
diff <(jq -S '.trajectories[].passed' evals/results/trajectories/results.json) \
     <(jq -S '.trajectories[].passed' /tmp/traj.json)
```

Latency figures will differ by machine; the pass/fail results and the governance
metrics (poisoning, leakage, staleness) should not.

## What would make these numbers meaningful

More trajectories, and agent outcomes. The trajectory suite grounds the
scenario mechanisms in what real runs actually recorded and retrieved — a
first step past synthetic fixtures, taken with placeholder agents on one
scripted project family. What would move the needle next: trajectories from
unrelated real projects dropped into the same fixture format, and A/B runs
with real agents that could honestly populate `task_completion`,
`verification_improvement`, and `review_cycle_reduction`. Until then the gap
remains the first item in [`LIMITATIONS.md`](LIMITATIONS.md).
