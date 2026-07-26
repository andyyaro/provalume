# Event schema

Events are the source of truth. Everything else is a projection.

Field-by-field detail is in [`DATA_MODEL.md`](DATA_MODEL.md); this document lists
the event types and what each one produces.

---

## The type set is closed

Deliberately. Deterministic writers map event types to memory candidates, so an
open-ended type would mean an event nothing knows how to project — stored, and
silently inert.

Adding a type means adding its writer and its tests. That friction is the point.

## Run and task lifecycle

| Type | Projects into |
|---|---|
| `run.started` | — |
| `run.completed` | episodic |
| `task.started` | — |
| `task.completed` | episodic, performance |
| `attempt.started` | — |
| `attempt.completed` | episodic, performance |

## Deterministic evidence

**These are the events that can promote a memory.** Everything else records
context.

| Type | Projects into | Payload |
|---|---|---|
| `verification.passed` | procedural, episodic | `command`, `exit_code`, `purpose`, `duration_s` |
| `verification.failed` | **gotcha**, episodic | `command`, `error_kind`, `excerpt`, `exit_code` |
| `command.succeeded` | procedural, episodic | as above |
| `command.failed` | gotcha, episodic | as above |

A `verification.failed` event is what a gotcha is made of. The failure **is** the
evidence, which is why a gotcha can be `trust_state=verified` with
`verification_state=failed`.

## Review

| Type | Projects into | Payload |
|---|---|---|
| `review.approved` | promotes to `reviewed` | `reviewer`, `subject`, `findings` |
| `review.rejected` | gotcha (lesson), episodic | `reviewer`, `subject`, `finding`, `files` |
| `review.changes_requested` | gotcha (lesson), episodic | as above |
| `review.finding` | gotcha | `finding`, `severity`, `reviewer`, `files` |

`reviewer` is compared against the record's `author_agent`. A self-review never
promotes, and the refusal is recorded.

A review verdict attaches by attempt or task association to **claim types**
(semantic, procedural, decision) only. To attach it to a gotcha or an episode, the
reviewer must name that record's `subject` — because approving a fix is not
approving the failure that prompted it.

## Human authority

| Type | Projects into | Payload |
|---|---|---|
| `human.decision` | **decision**, reaching `integrated` on authority | `question`, `selected`, `rejected`, `rationale`, `authority`, `consequences` |
| `human.promotion` | explicit promotion | `memory_id`, `target` |
| `human.invalidation` | invalidates | `memory_id`, `reason` |
| `human.rejection` | rejects permanently | `memory_id`, `reason` |

`rejected` is the reusable part of a decision: without it, nothing stops an agent
re-proposing what was already turned down.

A decision has no command to verify, so the human decision event is its evidence
at every rung. The rungs are still walked and still recorded — what differs is
what counts as evidence, not whether evidence is required.

## Git

| Type | Effect | Payload |
|---|---|---|
| `integration.landed` | Marks claim-type records landed; enables `integrated` | `target` (`run` or `user`), `branch`; `commit_sha` on the envelope |
| `integration.reverted` | Invalidates what it landed | `branch` |
| `commit.recorded` | Records a commit | — |
| `branch.rejected` | **Rejects every record on that branch, permanently** | `branch` |

`branch.rejected` is how abandoned work stops being truth while remaining
available as negative experience.

## Facts

| Type | Projects into | Payload |
|---|---|---|
| `fact.observed` | semantic | `statement`, `subject`, `category`, `evidence` |
| `fact.changed` | semantic, superseding the prior fact | as above, plus `replaces` |

`fact.changed` supersedes rather than overwrites. Without `replaces`, the
predecessor is found by matching `subject_key`.

## Agent-sourced — always untrusted

| Type | Projects into |
|---|---|
| `agent.proposal` | a quarantined memory of the requested type |
| `agent.observation` | quarantined semantic |
| `agent.failure_report` | quarantined gotcha |
| `agent.outcome_report` | quarantined episodic |

`source=agent` and the trust ceiling is `observed`; the landing state is
`quarantined`. Nothing in the payload changes that — a payload claiming
`{"verified": true, "confidence": "high"}` is payload.

## Warning feedback

| Type | Purpose |
|---|---|
| `warning.shown` | The gate warned |
| `warning.heeded` | The agent changed course |
| `warning.ignored` | It proceeded anyway |
| `warning.false_positive` | The warning was wrong |

These make the preflight gate's usefulness measurable rather than assumed. A gate
nobody can evaluate is a gate nobody should trust.

## Interface audit

| Type | Purpose |
|---|---|
| `mcp.call` | An MCP tool ran |
| `mcp.refused` | An MCP tool call was refused |
| `import.applied` / `import.rejected` | Interchange outcomes |

Refusals are recorded because a refused call is a security signal, and a
silently-dropped one is what an attacker wants.

## Freshness (ADR-0020)

**None of these can promote a memory.** They move the freshness axis only —
deliberately absent from the evidence set, so code-grounded invalidation
gains no trust authority. **Only kernel-sourced freshness events derive
anything**: an imported or agent-sourced freshness event is stored
append-only and moves nothing (threats T17, T28). A relevance verdict
answers one trigger; a record returns to `current` only when no trigger
remains outstanding.

| Type | Moves freshness | Payload |
|---|---|---|
| `blast_radius.recorded` | → `current` (the record becomes watchable) | `record_id`, `method` (`coverage` \| `import_graph` \| `commit_touch`), `paths`, `line_ranges` (optional), `tool`, `tool_version`; envelope `commit_sha` names the commit the radius was measured at |
| `freshness.triggered` | → `suspect`, unless a relevance verdict for the same trigger discharges it | `record_id`, `trigger_commit`, `changed_paths`, `changed_paths_total`, `intersecting_paths` — for a landing touching more than 2 000 paths, `changed_paths` records only the intersecting ones and `changed_paths_total` keeps the true count, so a monster commit cannot blow the admission cap and silently drop its own triggers |
| `relevance.assessed` | verdict `irrelevant` → back to `current`; `relevant` → stays `suspect` | `record_id`, `trigger_commit`, `verdict` (`relevant` \| `irrelevant`), `differ_version`, `reason_code` (closed enum: `whitespace_only`, `comment_only`, `docstring_only`, `signature_changed`, `body_changed`, `import_changed`, `unparseable`) |
| `reverification.executed` | outcome `passed` → `current`; `failed` → `stale`; `errored` → **no transition** (fail-open) | `record_id`, `trigger_commit`, `command`, `exit_code`, `duration_ms`, `timeout_ms` (the configured bound — a timeout kill must be distinguishable from an ordinary failure), `environment_fingerprint`, `outcome` (`passed` \| `failed` \| `errored`) |

`environment_fingerprint` is a hash over the interpreter version and the
dependency lockfile — without it, `stale` cannot distinguish "the code broke
this" from "the environment drifted".

`freshness.triggered` fires only for **landed** commits, consistent with the
rule that semantic truth requires a landing. Worktree state never triggers.
Only **kernel-sourced** freshness events participate in freshness derivation:
an agent-sourced or imported freshness event is stored append-only and
derives nothing (threats T17, T28).

These four types were declared ahead of their writers, deliberately: the
schema was locked at design time (ADR-0020) and the writers arrive milestone
by milestone. **The `blast_radius.recorded`, `freshness.triggered`, and
`relevance.assessed` writers are live**:
`record_verification` now attaches a radius to each claim record (procedural,
gotcha) it produces, extracted without executing the verification command or
any project code — read-only git plumbing only, fail-open, nothing recorded
for a git-less client. When the same record accrues several radius events (a
repeated failure re-anchors its gotcha), **the latest by journal order wins**
for freshness derivation. The watcher (`provalume freshness <sha>`)
triggers and assesses in one pass: trivia-only landings (whitespace,
comments, docstrings) discharge their own trigger and leave the record
`current`; everything else — including a file the differ cannot parse, a
change git reports but the text layer cannot see, and any trivia landing on
a record whose verification command reads comments or docstrings
(LIMITATIONS §9e) — stays `suspect`. A trigger can end up booked but
unassessed: the assessment failed open, or the intersection exceeded the
per-record bound (LIMITATIONS §9f). The CLI reports both rather than
claiming a clean pass, and a re-scan re-assesses a failed-open trigger. The
one remaining writer, `reverification.executed`, arrives with M4; until
then that event is stored and inert, which is the intended state.

## Recording events

The SDK's helpers cover the common cases and set `source` correctly:

```python
pv.record_verification(command=..., passed=..., excerpt=..., error_kind=...)
pv.record_review(reviewer=..., approved=..., subject=..., finding=...)
pv.record_decision(selected=..., rejected=[...], rationale=..., authority=...)
pv.record_fact(subject=..., statement=..., changed=False)
pv.record_integration(commit_sha=..., target="user")
pv.propose(text=..., memory_type=..., agent=...)     # always quarantined
```

For anything else:

```python
pv.record_event(EventType.RUN_COMPLETED, source=Source.KERNEL,
                payload={"outcome": "completed", "task_count": 4}, run_id="run-1")
```

`record_event` is the only path into the journal, and it runs the full admission
pipeline — validation, size caps, redaction, poisoning scan — before anything
durable happens. `Event.create` alone cannot persist.
