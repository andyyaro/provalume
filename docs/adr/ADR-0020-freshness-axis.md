# ADR-0020: The freshness axis and code-grounded invalidation

**Status:** Accepted · **Date:** 2026-07-26

## Context

A verified record is currently proven once and trusted until a human withdraws
it. A fact proven by running a command against commit `a1b2c3d` is a claim
about the code at `a1b2c3d`, not about the repository in perpetuity — yet
nothing in the model notices when the code the evidence exercised changes. A
digest line reading `[VERIFIED]` for a procedure whose covering file was
rewritten this morning is precisely the failure this project exists to
prevent, produced by this project.

Two existing mechanisms come close and must not be confused with this one:

- **Bi-temporal validity** (`valid_at`/`invalid_at`, threat T7) tracks
  *asserted* supersession — someone recorded that a fact changed. It does not
  notice code changing under a fact nobody has re-asserted.
- **Applicability** (CURRENT / HISTORICAL / CROSS_SCOPE / UNCERTAIN,
  ADR-0006) answers a *query-relative* question: how does this record's
  commit anchor relate to the commit being asked about, by ancestry. It says
  nothing about whether the evidence still covers the claim. A record can be
  applicability-CURRENT (its anchor is an ancestor of the queried commit)
  while the exact lines its verification executed were rewritten in between.
  Note the deliberate naming collision: `Applicability.CURRENT` and
  `FreshnessState.CURRENT` share the string value `"current"` while meaning
  different things; comparisons must be enum-typed, and rendered labels must
  be axis-qualified so a digest reader can never mistake one for the other.

The missing question is: **does the code still support this record's
evidence?** That is a property of the record against the landed history, not
of any query.

## Decision

**Freshness is a second, independent axis. It is not a trust rung.**

```
trust:     quarantined | observed | verified | reviewed | integrated
freshness: current | suspect | stale | unverifiable
```

Trust answers *how well was this proven, and by whom*. Freshness answers *does
the code still support it*. The dangerous case this feature exists to surface
is `integrated` × `stale` — highest trust, lowest freshness — and a single
ladder cannot express it. `needs-reverification` as a sixth rung was
considered and rejected: it would make freshness transitions look like trust
transitions, hand the promotion machinery a state it has no rules for, and
destroy the invariant that trust only moves on evidence or judgement.

### Semantics

- **`current`** — a blast radius is recorded and no landed commit has touched
  it since the record's evidence was produced (or the most recent touch was
  assessed irrelevant, or a re-run passed after it).
- **`suspect`** — a landed commit touched the blast radius, and the change has
  not been ruled irrelevant or survived a re-run. Suspect is an invitation to
  check, not an assertion of falsehood.
- **`stale`** — a re-execution of the record's own command was performed under
  the operator-gated executor and failed. A machine observation, with the
  environment fingerprint recorded so the observation is attributable.
- **`unverifiable`** — the machine cannot make a freshness claim: the record
  has no re-runnable command, the command's environment no longer resolves,
  or no blast radius was ever recorded (legacy records predating this
  feature, and records whose extraction failed open). Claiming `current`
  in any of these cases would assert "nothing changed underneath this"
  without the means to know; the project's standing convention is honest
  uncertainty (LIMITATIONS §7), and this follows it.

### `stale` is not `invalidated`

The distinction is axis and terminality, not who acted. `invalidated` is a
**terminal trust state**: the record leaves the ladder and is never again
served as truth. It is reached by reviewer judgement — and also by the
existing machine rule `invalidate.commit_reverted`, when the landing a record
depended on is reverted; the machinery is not human-only and this ADR does
not pretend otherwise. `stale` is a **freshness label on a live record**: the
record keeps its trust state, keeps its history, keeps being served — with
the label attached — and the next landing whose re-run passes returns it to
`current`. The freshness machinery can never produce `invalidated` or any
other trust transition, and invalidation does not consult freshness. Merging
the two would let a failing re-run silently exercise withdrawal authority.

### Derivation

Freshness is a projection over journal events, like every other piece of
current state (I2, I3). No freshness value is stored authoritatively; the
rebuild derives it. The transition sources:

| Event (wire name) | Effect on freshness |
|---|---|
| `blast_radius.recorded` | record becomes `current` (it is now watchable); when a record accrues several radii — a repeated failure re-anchors its gotcha — the latest by journal order is the one derivation and intersection read |
| `freshness.triggered` | record becomes `suspect`, unless a relevance verdict for the same trigger says otherwise |
| `relevance.assessed` (verdict `irrelevant`) | the trigger is discharged; record returns to `current` |
| `relevance.assessed` (verdict `relevant`) | record stays `suspect` |
| `reverification.executed` (outcome `passed`) | record returns to `current` |
| `reverification.executed` (outcome `failed`) | record becomes `stale` |
| `reverification.executed` (outcome `errored`) | **no transition** — fail-open (I5): the engine's own failure is never evidence about the record |

**Only kernel-sourced events participate in this derivation.** A freshness
event arriving with any other `source` — an agent, or a JSONL import — is
stored append-only like every event, and derives nothing: an imported
`freshness.triggered` must not be able to relabel a local record any more
than an imported claim can raise its trust (threats T17, T28). The engine
emits as the kernel because, like the original verification, its authority
comes from having run the computation itself.

A reverting commit needs no special case: it lands, triggers, is assessed
relevant, re-runs pass, and the record returns to `current` through the
ordinary cycle.

### Event vocabulary

Wire names follow the journal's dotted convention; the specification's
underscore names map one-to-one:

| Spec name | Wire name | Payload |
|---|---|---|
| `blast_radius_recorded` | `blast_radius.recorded` | `record_id, method (coverage\|import_graph\|commit_touch), paths[], line_ranges[]?, tool, tool_version` — the commit the radius was measured at travels as the envelope `commit_sha`, per the journal's existing convention |
| `freshness_trigger` | `freshness.triggered` | `record_id, trigger_commit, changed_paths[], intersecting_paths[]` |
| `relevance_assessed` | `relevance.assessed` | `record_id, trigger_commit, verdict (relevant\|irrelevant), differ_version, reason_code` |
| `reverification_executed` | `reverification.executed` | `record_id, trigger_commit, command, exit_code, duration_ms, timeout_ms, environment_fingerprint, outcome (passed\|failed\|errored)` — `timeout_ms` is the configured bound, recorded so a kill-by-timeout is distinguishable from an ordinary failure |

`reason_code` is a closed enum — `whitespace_only`, `comment_only`,
`docstring_only`, `signature_changed`, `body_changed`, `import_changed`,
`unparseable` — because a deterministic differ has enumerable reasons, and
free text here would be unauditable. `unparseable` escalates; when the differ
cannot read a file, it does not get to call the change harmless.

`environment_fingerprint` is a hash over the interpreter version and the
dependency lockfile. Without it a `stale` verdict cannot be attributed —
"the code broke this" and "the environment drifted" become
indistinguishable, and the feature becomes unfalsifiable.

Blast-radius `method` is recorded because the three extraction methods carry
very different evidential weight: `coverage` observed what actually ran;
`import_graph` bounds what could run; `commit_touch` merely names what
changed alongside. Downstream consumers may weight them; they may not
conflate them.

### Surfacing

Retrieval results, the rendered digest, and the CLI surface **both axes** on
every record that carries them. Freshness never alters trust state,
bi-temporal validity, or truth-presentability — it is presented alongside
them. Ranking may apply a bounded demotion to `suspect` and `stale` records;
no record is ever excluded from retrieval on freshness alone. Suppressing
suspect records would convert a labelling mechanism into a deniability
mechanism (threat T28); the point is that the reader sees
`[INTEGRATED · STALE]` and decides.

### Execution posture

Restated from the threat model because it is load-bearing (T27–T29):

- Triggers compute from **landed commits only** — worktree state never
  triggers, consistent with the rule that semantic truth requires a landing.
- There is **no daemon**. Triggering and execution happen inside an explicit
  CLI invocation or an operator-installed hook.
- Re-execution is **off by default**: the allowlist ships empty and an empty
  allowlist disables the executor. Only records at trust `verified` or above
  are eligible. Argument vectors only, hard timeout, fingerprint recorded.
- Relevance filtering is a deterministic AST comparison (stdlib `ast`),
  covered by the ADR-0007 no-LLM/no-network guard. Model-assisted relevance
  judgement is a design change requiring escalation, not an implementation
  option (I1).

## Consequences

- The journal grows by trigger volume (bounded, T29); projections gain a
  freshness component that must rebuild byte-identically (I3) — re-run
  *results* are journal inputs, and `rebuild` never executes a command.
- Python-only at first. The multi-language differ abstraction is deliberately
  not built until the single-language precision numbers exist.
- Precision is measured (M5) against labels produced independently of the
  implementation, with a kill criterion decided before the numbers arrive:
  if false invalidation is high enough that users would ignore freshness
  warnings, gated re-execution does not ship. Suspect marking can still ship
  alone — an invitation to check carries a far lower precision burden.
- Original verifications recorded before this feature carry no environment
  fingerprint and no blast radius; they surface as `unverifiable` rather
  than being grandfathered as `current`.
