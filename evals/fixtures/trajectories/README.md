# Trajectory fixtures

Call logs captured from **real Orkestra dogfood runs**, replayed by the
trajectory suite (`provalume eval --suite trajectories`, engine in
`src/provalume/evals/trajectories.py`, decision record in
[ADR-0019](../../../docs/adr/ADR-0019-trajectory-benchmark.md)).

Each fixture directory is a frozen pair:

| File | What it is |
|---|---|
| `calls.jsonl` | Every `OrkestraAdapter` call the run made, in order — method, kwargs, and (for reads) the value returned at capture time. Captured by `capture/sitecustomize.py` wrapping the adapter during a scripted scenario. |
| `expectations.json` | What memory owed the agent at each decision point, authored by hand against the captured evidence. |
| `probes-at-capture.json` | Post-state observations recorded at capture time (`capture/probe.py`) — the evidence the expectations were authored from. Not read by the harness. |

## Provenance

Captured 2026-07-26 (UTC) on macOS against provalume 0.1.4 (installed from
PyPI) and orkestra-runtime 0.5.2 on the `provalume-memory-integration` branch
(@ `18457c2`), using the `fake` practice adapters. The runs were real end to
end — real git worktrees, real failing pytest gates, real retries, a real
decision gate, real integration commits. Two things were scripted and are
disclosed as such: the practice agents write placeholders, so **attempt
outcomes in these logs say nothing about memory quality and are never scored
as if they did** (LIMITATIONS §1), and the decision gate's answer was chosen
by the capture script driving `orkestra approve`, not by a person, although
Orkestra records it with human authority.

Captured content is verbatim: command strings and stderr excerpts contain the
capture machine's absolute paths. Rewriting them would risk changing signature
matching behaviour, so they stay as the run produced them.

## `calls.jsonl` format

One JSON object per line:

```json
{"ts": 1753500828.5, "pid": 4242, "method": "verification",
 "args": [], "kwargs": {"command": "…", "passed": false, "excerpt": "…"}}
```

- `method: "__init__"` lines carry the `OrkestraContext` under `"context"` and
  delimit client segments (one per Orkestra process that opened memory).
- Read calls (`brief_digest`, `preflight`) also carry `"result"` — what 0.1.4
  returned at capture time. **Evidence, not oracle:** the harness scores
  against `expectations.json`, so an intentional improvement in HEAD does not
  fail the suite and a regression does. Replay does compare each read against
  the captured return and reports divergence as a note (never a failure) —
  the known divergence is the `git=None` fidelity limit in ADR-0019, visible
  as three digest drift notes in the committed results.
- Every lossy path in the shim appends a `…[truncated]` sentinel (strings cut
  at 20 000 characters, `repr` fallbacks), and the loader refuses a fixture
  containing one anywhere in a call, because replay could not be faithful.
  In practice nothing approaches the limits: the integration slices excerpts
  to 8 000 characters before they reach the adapter, and the schema caps
  stored excerpts at 8 192.

## `expectations.json` format

```json
{
  "trajectory": "fix-lands",
  "competencies": ["premise-awareness"],
  "description": "…",
  "captured": {"date": "…", "provalume": "…", "orkestra": "…", "scenario": "…"},
  "checkpoints": [
    {"call": 7, "expect": {"matched": true, "occurrences": 1, "resolved": false}}
  ],
  "probes": [
    {"kind": "recall", "query": "uploader retry", "expect": {"total": 0},
     "known_limitation": "LIMITATIONS.md §13: …"}
  ]
}
```

**Checkpoints** reference captured read calls by line index and score them at
replay. `preflight` expectations: `matched`, `occurrences` (exact, against the
top-confidence match), `min_confidence`, `resolved`, and
`resolution_names_commit_of_call` — an index into `calls.jsonl` whose
`commit_sha` the match's `resolution_commit_sha` must equal, kept indirect so
a regenerated capture stays self-consistent. `brief_digest` expectations:
`items` (exact), `min_items`, `contains`; the budget check is implicit.

**Probes** run after the full replay, against the final state: `preflight`
(command taken from a captured call via `command_of_call`), `digest`, `recall`
(`total`/`min_total`, `top_type`, `top_trust`), and `ladder`
(`expect_counts` of memory records by type and trust state, `min`/`max`).

A probe carrying `known_limitation` **passes by missing**: it pins a
documented gap (e.g. LIMITATIONS §13, lexical retrieval cannot find a gotcha
by feature description) as an assertion that fails if the behaviour moves,
and it touches **no counter at all** — whatever its kind — so a designed miss
never distorts a rate in either direction.

Every expectation file is schema-validated at load: unrecognised keys, empty
expectations, an empty `contains` list, a ladder entry with neither `min` nor
`max`, or a fixture with no checkpoints are refused, because a check that can
silently assert nothing is this project's oldest defect class.

Metric wiring — checkpoints and non-limitation probes feed the same counters,
and the published tables state the composition per metric: should-warn
expectations feed `repeated_error` (a hit is a *silent* gate), should-not-warn
feed `false_warnings`, occurrence checks feed `occurrence_fidelity` (a silent
gate scores a miss here too, so the denominators stay aligned), post-landing
naming checks feed `resolution_surfacing`, digest `contains` checks feed
`digest_inclusion`, non-limitation recall probes feed
`recall_coverage`/`recall_precision`. Every rate keeps its denominator.

The harness also verifies, per trajectory, that every replayed write landed in
the journal **in the right envelope and with nothing extra**: expected events
are keyed (type, run id, task id) from the call log, the comparison is
two-way — a write under the wrong task, or an event type the calls never
implied, is a failure — and `warning.shown` is checked as an aggregate count,
one per recorded matching decision point (every `record=True` preflight call
in the committed fixtures is covered by a checkpoint, which is what makes the
aggregate tight). A deep audit must pass on the replayed database.

## The four fixtures

| Fixture | Competencies | Story |
|---|---|---|
| `fix-lands` | premise-awareness, workflow-knowledge, dynamic-state-tracking | Gate fails twice, fix lands in run 2, resolution presented at every later decision point |
| `repeat-blocked` | premise-awareness, dynamic-state-tracking | Four failures across two runs, no fix; occurrences accumulate; the scripted abort decision is remembered |
| `env-gotcha` | environment-gotchas, workflow-knowledge | `ModuleNotFoundError` at collection; recallable by error text, invisible to a description query |
| `two-command-gate` | premise-awareness, workflow-knowledge | Two-command gate; warns about the failing command at every decision point, never about the passing one |

## Regenerating

```sh
ORKESTRA_VENV=/path/to/orkestra-venv bash capture/run_scenarios.sh /tmp/captures
```

Capture is not deterministic — commit SHAs, run ids, and event ordering details
change each time — so a regenerated `calls.jsonl` needs its expectations
re-authored against the new evidence (use `capture/probe.py` for the
post-state). The committed fixtures are a frozen, internally consistent pair.
