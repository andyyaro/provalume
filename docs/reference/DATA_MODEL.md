# Data model

Every field, and why it exists.

---

## Events

The source of truth. Append-only, enforced by database triggers. Everything else
is a projection ([ADR-0002](../adr/ADR-0002-immutable-event-journal.md)).

| Field | Type | Required | Purpose |
|---|---|---|---|
| `event_id` | ULID, 26 chars | yes | Identity. Time-sortable so JSONL diffs stay append-mostly; collision-resistant so two machines can record offline and merge. |
| `seq` | int | assigned | Local insertion order. Dense and monotonic; **not** the identity. |
| `schema_version` | int | yes | So a rebuild applies the rules current when the event was recorded, rather than reinterpreting old evidence under new policy. |
| `event_type` | enum | yes | Closed set. An open type would mean events nothing knows how to project. |
| `recorded_at` | RFC 3339 UTC ms | yes | When Provalume learned it |
| `occurred_at` | RFC 3339 UTC ms | no | When it happened, if different |
| `project_id` | str | **yes** | The isolation boundary. `NOT NULL`, filtered on every query path, no bypass. |
| `repository_id` | str | no | Remote URL where available; stable across clones |
| `run_id` / `task_id` / `attempt_id` | str | no | Orchestration provenance |
| `agent_profile` | str | no | Who acted — used for independence checks |
| `adapter` / `model` / `effort` | str | no | Which tooling produced it |
| `branch` | str | no | Branch scope |
| `worktree` | str | no | Distinguishes concurrent contradictory worktrees |
| `base_commit` / `commit_sha` | hex | no | Commit validity. Validated as hexadecimal: a non-SHA cannot be resolved, so accepting one would produce provenance that looks checkable and is not. |
| `causal_parent_event_id` | ULID | no | Causal chain — e.g. an outcome linked to the warning that preceded it |
| `source` | enum | yes | **Structural.** Assigned by the code path, never read from content. |
| `payload` | JSON | yes | The evidence itself |
| `payload_hash` | `sha256:…` | assigned | **Globally stable.** Same payload, same hash, any machine. |
| `event_hash` | `sha256:…` | assigned | **Locally chained.** Tamper-evident within this database. |
| `prev_event_hash` | `sha256:…` | assigned | The chain link |
| `redaction` | JSON | assigned | What fired — never the secret itself |
| `integrity` | JSON | assigned | Poisoning risk, signature metadata |

### Why two hashes

`payload_hash` is identity: it answers "is this the same content?" across
machines, which is what makes duplicate detection possible on import.

`event_hash` is tamper-evidence *here*: it chains each event to its predecessor.
Imported events are appended in arrival order and chained into the receiving
database's own sequence, so the chain is deliberately **not** a global ledger.
`seq`, `event_hash`, and `prev_event_hash` are therefore never exported.

### Sources and their ceilings

| `source` | Trusted to | Highest state it can produce directly |
|---|---|---|
| `human` | Everything | `integrated` |
| `kernel` | Report deterministic outcomes, not interpret them | `verified` |
| `adapter` | Same as kernel, subject to its own validation | `verified` |
| `agent` | Nothing. Its reports are claims. | `observed` |
| `import` | Nothing. A signature proves origin, not truth. | `observed` |

## Memories

Projections. Mutable and rebuildable — which is why they carry a `content_hash`:
unlike events there is no trigger stopping a direct edit, and the hash is what
makes one visible to `audit`.

| Field | Purpose |
|---|---|
| `memory_id` | Derived deterministically from the originating event, prefixed by kind, so a rebuild reproduces identifiers rather than minting new ones |
| `memory_type` | One of six categories |
| `content` | Structured. What policy and filters reason over. |
| `text` | Rendered. What goes into a digest. |
| `scope` | Project, repository, branch, run, task, attempt, agent |
| `source_event_ids` | The evidence chain. A reference that does not resolve is a hard `audit` failure. |
| `source` | Inherited from the originating event |
| `author_agent` / `adapter` / `model` / `effort` | Who produced it |
| `commit_sha` | Commit anchor for validity |
| `valid_at` / `invalid_at` / `recorded_at` | Bi-temporal validity |
| `supersedes_id` | Linear chain to the record this replaced |
| `trust_state` | One of eight |
| `verification_state` | `unknown` / `passed` / `failed` |
| `review_state` | `none` / `approved` / `changes_requested` / `rejected` |
| `integration_state` | `none` / `integrated_run` / `accepted_user` / `reverted` |
| `access_count` / `last_accessed_at` | Usage signal for ranking |
| `content_hash` | Over both `content` and `text` |
| `redaction` | What fired |
| `poisoning_risk` / `poisoning_matches` | Which heuristics matched, so `explain` can say why |
| `subject_key` | Normalised subject, for supersession and contradiction detection |

### Why `content` and `text` are both stored

`content` is what filters and policy read; `text` is what a digest renders.
Deriving one from the other at read time would make ranking depend on formatting,
and would make a rendering change invisible to the rebuild-determinism tests.

## The six categories

| Type | Contents | Write trigger | Ceiling without landed history |
|---|---|---|---|
| `episodic` | Attempts, failures, repairs, reviews, run outcomes | Deterministic projection | `verified` |
| `semantic` | Current repository facts, environment, conventions, architecture | Landed integration, or human decision | `reviewed` — **needs `integrated` to be current truth** |
| `procedural` | Verified commands, runbooks, repair/test/release procedures | One passing run of the *exact* command | `verified` |
| `decision` | Selected option, rejected alternatives, rationale, authority | A human decision event | `integrated` when `source=human` |
| `gotcha` | Failed approach, failure signature, context, later resolution | A verification-failure event | `verified` — **never promoted to semantic truth** |
| `performance` | Agent/profile evidence per task category | Deterministic aggregation | `verified` |

Working memory is not stored. It is the digest, composed at query time — a stored
copy would be a second, staler source of truth and a second thing to poison.

### Which category?

The ambiguous case is common enough to have a rule:

> **If it describes something that *failed*, it is a `gotcha`. If it describes
> what *is*, it is `semantic`.**

| Statement | Category | Why |
|---|---|---|
| "`pytest -n auto` deadlocks in the db fixture" | `gotcha` | Describes a failure |
| "The integration suite runs serially" | `semantic` | Describes what is |
| "Run `pytest -p no:xdist` for integration tests" | `procedural` | A command that passed |
| "We chose serial over parallel because the fixture is not safe" | `decision` | A human choice with rejected alternatives |
| "Attempt 3 failed, attempt 4 passed" | `episodic` | A record of what happened |
| "agent-A succeeds at 4/5 migration tasks" | `performance` | An aggregate |

### Claim types versus record types

| Group | Types | Behaviour |
|---|---|---|
| **Claim** | `semantic`, `procedural`, `decision` | Assert something about the codebase. Can be reviewed and landed. |
| **Record** | `episodic`, `gotcha`, `performance` | Record an occurrence. Review and integration verdicts do **not** attach by attempt association. |

This distinction matters more than it looks. Without it, a review approving a fix
gets stamped onto every record sharing the attempt — including the failure that
prompted the fix — so a gotcha ends up reading "approved by reviewer-2" and, once
the branch merges, "integrated". Both are false: the reviewer approved the fix,
and what landed was the fix. A reviewer can still confirm a finding by naming its
subject explicitly.

## Trust states

Five ranked rungs plus three unranked terminal states:

```
LADDER    quarantined(1) → observed(2) → verified(3) → reviewed(4) → integrated(5)
TERMINAL  invalidated    superseded    rejected
```

Terminal states are unranked because "is `rejected` more trusted than `observed`?"
is a category error. Full semantics in
[`TRUST_MODEL.md`](../security/TRUST_MODEL.md).

### The coherent-looking contradiction

A gotcha can be `trust_state=verified` with `verification_state=failed`. Both are
true: the evidence is real, and the evidence is a failure. This is the case that
forced trust state and evidence state to be separate fields, and it is the thing
most often reported as a bug.

## Transitions

No memory changes state without a row here — the pairing is written in one
transaction, so a promoted record with no audit trail is not representable.

| Field | Purpose |
|---|---|
| `transition_id` | Monotonic within a millisecond, so a three-rung promotion prints in order |
| `memory_id`, `from_state`, `to_state` | The change |
| `policy_rule` | **The named rule.** What makes the trust model falsifiable rather than decorative. |
| `evidence_event_ids` | The specific events relied on |
| `actor`, `actor_source`, `recorded_at`, `scope` | Who, when, where |
| `allowed` | **`false` for refusals.** A promotion attempt that vanishes silently is what an attacker wants. |
| `note` | The reason, in prose |

## Supporting tables

| Table | Purpose |
|---|---|
| `journal_head` | Chain head, seq, count — appending needs no scan, and rollback is detectable if you pinned the head |
| `failure_signatures` | Signature → occurrence count → resolution. Repetition is what turns a note into a warning. |
| `contradictions` | Detected pairs. Never auto-resolved: recency is not correctness, and the newer record may be the poisoned one. |
| `memory_links` | Gotcha ↔ resolution, supersession edges |
| `memory_vectors` | Optional embeddings, keyed by `model_id` because distances from two models are not comparable |
| `projection_state` | How far projections have caught up |
| `projects` | Registry, so `doctor` can report contents without scanning |

## Scope

```
project → repository → branch → run → task → attempt → agent
```

Widening is a promotion with its own evidence: branch → repository needs landed
integration, repository → project needs human approval, and `global` is
**unreachable** — the value is reserved so adding it later is a policy change
rather than a migration, and no rule targets it
([ADR-0016](../adr/ADR-0016-global-memory-deferral.md)).

## Applicability

Reported per result, never guessed:

| Value | Meaning |
|---|---|
| `current` | Valid at the queried commit and scope |
| `historical` | Introduced on another line of history |
| `cross_scope` | From a different branch or repository |
| `uncertain` | Ancestry could not be determined — rebased, cherry-picked, garbage-collected, or no repository |

`uncertain` is used freely and is not a failure. Git ancestry answers "could this
have been true here?", not "is this true here?" — and a labelled uncertainty beats
a confident wrong answer.

## Freshness

A second per-record axis, orthogonal to trust
([ADR-0020](../adr/ADR-0020-freshness-axis.md)): trust answers *how well was
this proven*, freshness answers *does the code still support it*. Derived
from journal events, never stored authoritatively; the behaviour that moves
it arrives milestone by milestone with the code-grounded invalidation work.

| Value | Meaning |
|---|---|
| `current` | A blast radius is recorded and no landed commit has touched it since the evidence was produced |
| `suspect` | A landed commit touched the blast radius; not yet ruled irrelevant or survived a re-run. An invitation to check, not an assertion of falsehood |
| `stale` | A gated re-execution of the record's own command failed, with the environment fingerprint recorded |
| `unverifiable` | The machine cannot make a freshness claim: no re-runnable command, an unresolvable environment, or no recorded blast radius (every record predating the axis) |

Note the naming collision with applicability: both axes have a `current`.
They answer different questions — applicability is query-relative ancestry,
freshness is record-relative code change — and rendered labels are
axis-qualified so the two can never be mistaken for each other.
