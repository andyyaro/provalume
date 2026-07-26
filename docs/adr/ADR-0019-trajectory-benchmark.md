# ADR-0019: Trajectory benchmark

**Status:** Accepted · **Date:** 2026-07-26

## Context

The eval harness (twenty scenarios, [`BENCHMARKS.md`](../reference/BENCHMARKS.md))
proves mechanisms on synthetic fixtures written by the same people who wrote the
mechanisms. [`LIMITATIONS.md`](../reference/LIMITATIONS.md) §1 names the gap:
nothing yet measures Provalume against what real orchestration runs actually
produce. Three metrics ship with a deliberate zero denominator for the same
reason.

Dogfooding has now produced real trajectories: end-to-end Orkestra runs with
real git worktrees, real failing verification commands, real retries, a real
decision gate (answered by the capture script), and a real integration
landing. Each run drives Provalume through
`provalume.integrations.orkestra.OrkestraAdapter` — a surface small enough to
capture completely — plus `Provalume.open`/`close` at run boundaries, which is
where the real git repository enters and the one call replay must substitute.

LongMemEval-V2 names the competencies that matter for coding agents — workflow
knowledge, environment gotchas, dynamic state tracking, premise awareness — and
is the shape (not the score) this project has said it would follow.

## Decision

**Ship a trajectory suite: frozen call logs captured from real Orkestra runs,
replayed through the live adapter surface, scored at decision points against
authored expectations.**

### Fixtures are captured, not authored

A trajectory fixture is the ordered log of every `OrkestraAdapter` call a real
run made — method, kwargs, and (for reads) the returned value — captured by a
shim that wraps the adapter during a scripted dogfood scenario. The scenario
script is authored; the calls, excerpts, timing, and multiplicity are whatever
the run actually did. Fixtures live in `evals/fixtures/trajectories/<name>/`
with the expectations that score them.

### Replay drives the live write path, not JSONL import

`provalume replay`/`import_records` cannot be the mechanism: admission policy
caps imported events at `observed` and discards claimed trust, by design
(ADR-0011). The suite instead re-executes the captured calls against a fresh
database through the same `OrkestraAdapter` methods the integration uses, so
recording, projection, promotion, and retrieval all run live — the paths where
every real defect in this project has lived.

### Scoring happens at decision points

A decision point is a captured read — `brief_digest` or `preflight` at the
moment an orchestrator was about to dispatch work. Expectations state what
memory owed the agent at that moment, derived from the trajectory itself:

- a prior unresolved failure of this command existed → the gate must warn,
  with an accurate occurrence count (premise awareness);
- nothing relevant existed → the gate must stay silent (false-warning control);
- a landing resolved the failure → the match must present `what_later_worked`
  naming the landing, not a fresh warning (workflow knowledge, dynamic state);
- the relevant record must fit inside the digest at the configured budget
  (environment gotchas are only useful if they arrive in the brief).

Post-trajectory probe queries — authored, marked as probes — ask what memory
holds after the dust settles. They feed the same counters as decision points
(the published composition is stated per metric in `BENCHMARKS.md`), except
probes marked `known_limitation`: those exercise a documented gap, pass by
missing, touch no counter, and surface as notes — a gotcha keyed on command
and error is expected *not* to be findable by feature description under
lexical retrieval (LIMITATIONS §13), and the fixture fails if that ever
silently changes.

### Results follow the existing conventions

Boolean checks per trajectory, counters with visible denominators, a committed
results file under `evals/results/trajectories/`, and no comparison against any
other system in any output string. The suite runs from the same CLI as the
scenario suite.

## What this does not measure

Replay establishes what was **recorded and retrieved** on real trajectories. It
does not establish that an agent *reads* the digest and does better —
`task_completion`, `verification_improvement`, and `review_cycle_reduction`
keep their zero denominators, because populating them honestly requires A/B
runs with real agents, which remains open in LIMITATIONS §1. The practice
agents in captured runs are scripted placeholders; their attempt outcomes say
nothing about memory quality and are not scored as if they did.

## Fidelity limits, stated

- **Order-preserving, not interval-preserving.** The adapter surface does not
  accept explicit timestamps, so replay compresses minutes into milliseconds.
  Call order is preserved exactly; the score's recency component cannot move
  at these scales, and score ties break on millisecond timestamps, which
  today resolve in the same direction as at capture. A fast enough machine
  could collapse a tie into the same millisecond, where ordering falls to a
  per-run identifier — a latent flakiness risk, not an observed one.
- **No repository at replay — a write-side divergence, not just a read-side
  fallback.** Clients are constructed with `git=None` (the determinism
  convention of the eval suite), so replay does not stamp the
  `repository_id`/`branch`/`commit_sha` defaults the live client stamped onto
  every event. Stored episodic texts lose their `at <sha>` anchors, and
  commit-anchored records evaluate applicability as UNCERTAIN — the query
  names no commit — where the live run saw CURRENT or HISTORICAL. The
  replayed digests are measurably less informative than the captured ones at
  exactly the checkpoints that carry the most content; replay compares every
  read against the captured return and reports each divergence as a note, so
  the gap is visible per run rather than silent. Expectations never depend on
  ancestry evaluation — which also means the CURRENT/HISTORICAL/UNCERTAIN
  labelling itself is outside what this suite can regress-test.
- **Captured returns are evidence, not oracle.** Fixtures record what 0.1.4
  returned at capture time; scoring is against authored expectations, so a
  legitimate improvement in HEAD does not fail the suite, and a regression
  does. Divergence from the captured returns is reported as notes, never
  scored.

## Alternatives rejected

- **Synthetic trajectory authoring** — indistinguishable from the existing
  twenty scenarios; the point is content this project did not write.
- **Replay via JSONL import** — trust-capped at admission; would test the
  import gate, not the integration path.
- **Live Orkestra runs in CI** — nondeterministic, slow, and a circular
  dependency on the downstream integration; capture once, replay forever.
