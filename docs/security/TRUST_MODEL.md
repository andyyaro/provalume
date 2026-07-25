# Provalume trust model

This is the specification the implementation follows. If code and this document
disagree, the code has a bug.

Provalume's claim is "facts your agents proved, not things they said." That claim
is only worth anything if there is a mechanical, auditable answer to *proved by
what?* for every record. This document gives that answer.

---

## 1. The one rule

> **Trust is granted by deterministic evidence from a trusted source, never by
> assertion — no matter how confident the assertion sounds.**

Everything below is that rule, made specific.

Three corollaries that shape the whole design:

1. **An agent cannot promote its own work.** Not through the SDK, not through the
   CLI running as an agent, not through MCP. The party that produces a claim is
   never the party that grants it trust.
2. **Verification passing is necessary but not always sufficient.** A command
   exiting 0 in one worktree proves something happened there. It does not prove the
   project has changed. Project truth needs landed history.
3. **Negative results are first-class.** A rejected approach is not a failure of
   memory, it is the most valuable thing memory holds. Rejection is preserved as
   experience and permanently barred from being current truth. These are different
   things and the model keeps them separate.

## 2. Trust states

Eight states. Each record is in exactly one.

| State | Meaning | Presentable as current project truth? |
|---|---|---|
| `quarantined` | Admitted and stored, but its content came from an untrusted source with no supporting evidence, or it tripped a poisoning heuristic. Retrievable only when explicitly requested, always labelled. | **No** |
| `observed` | Something was recorded as having happened, from a source trusted to report but not to interpret. The default for agent-sourced structured reports. | **No** |
| `verified` | A deterministic evidence event supports it: a command ran and its outcome is recorded. Includes gotchas, whose evidence is a *failure*. | Within its own scope, labelled |
| `reviewed` | An independent party — a reviewing agent's structured verdict, or a human — assessed it. Independence is required: the reviewer must not be the author. | Within its own scope, labelled |
| `integrated` | The work it describes landed in history: merged into the run's integration branch, or accepted into the user's branch. The only state that can be presented as current project truth without qualification. | **Yes** |
| `invalidated` | Was true, is no longer. Retained with `invalid_at` set. Retrievable for historical queries. | **No** |
| `superseded` | Replaced by a specific newer record, linked by `supersedes_id`. Retained; the chain is walkable. | **No** |
| `rejected` | The work was rejected, or the claim was disproved. Retained permanently as negative experience. **Can never be promoted.** | **No, ever** |

### Two dimensions, not one

`quarantined → observed → verified → reviewed → integrated` is a **ladder**:
ordered, and `min_trust` filtering compares rank along it.

```python
TRUST_RANK = {"quarantined": 1, "observed": 2, "verified": 3,
              "reviewed": 4, "integrated": 5}
```

`invalidated`, `superseded`, and `rejected` are **terminal**: not ranked, excluded
from ordinary retrieval, and never promoted. They carry no rank because the
question "is this more trusted than `observed`?" is not meaningful for a record
that has been withdrawn.

Terminal states are reachable from any ladder state. Ladder states are never
reachable from a terminal state — with one narrow exception, stated in §6.

### Trust state is not the same as evidence state

Three independent evidence fields are recorded alongside the trust state, because
collapsing them loses information the ranking policy needs:

| Field | Values |
|---|---|
| `verification_state` | `unknown`, `passed`, `failed` |
| `review_state` | `none`, `approved`, `changes_requested`, `rejected` |
| `integration_state` | `none`, `integrated_run`, `accepted_user`, `reverted` |

A gotcha memory is the case that proves the separation matters:
`trust_state=verified` (a deterministic evidence event supports it) while
`verification_state=failed` (the evidence *is* a failure). Both are true. One
field could not say it.

## 3. Sources

Every event records a `source`. It is structural — assigned by the code path that
created the event, never chosen by the content.

| `source` | Who | Trusted to |
|---|---|---|
| `human` | The operator, at the CLI or through a human-decision gate | Everything. The highest authority in the system. |
| `kernel` | An orchestration kernel reporting a structured outcome it deterministically observed | Report facts (exit codes, verdicts, commit SHAs) — **not** to interpret them |
| `adapter` | An integration translating a kernel's native records | The same as `kernel`, subject to the adapter's own validation |
| `agent` | An autonomous agent, including through MCP | Nothing. Its reports are recorded as claims. |
| `import` | A JSONL file from elsewhere | Nothing. Signature verification proves origin, not truth. |

The ceiling on what a source can *directly* produce:

| `source` | Highest state it can produce directly |
|---|---|
| `human` | `integrated` |
| `kernel` / `adapter` | `verified` |
| `agent` | `observed` |
| `import` | `observed` |

To go above its ceiling, a record needs additional evidence events from a
source with sufficient authority. That is what promotion is.

## 4. Promotion

Promotion is a distinct, audited operation. It is never a side effect of a write.

### Requirements per transition

| Transition | Requires |
|---|---|
| `quarantined → observed` | A deterministic evidence event linking the record to a real run/task/attempt, plus `poisoning_risk` below the configured threshold. Clears the "no supporting evidence" condition. |
| `observed → verified` | A verification-result event (`source` in {`kernel`, `adapter`, `human`}) whose subject matches the record. For procedural memory the *exact* command must match. |
| `verified → reviewed` | A review-verdict event from a party that is **not** the record's author. Author identity is compared on `agent_profile`; a self-review is refused, and the refusal is recorded. |
| `reviewed → integrated` | An integration event with a resolvable `commit_sha`, and `integration_state` in {`integrated_run`, `accepted_user`}. |
| any → `invalidated` | An event showing the fact no longer holds, or an explicit human invalidation. |
| any → `superseded` | A newer record covering the same subject, linked by `supersedes_id`. Both persist. |
| any → `rejected` | A rejection verdict, or an explicit human rejection. Terminal. |

Skipping a rung is not allowed. `observed → integrated` in one step is refused even
when an integration event exists, because the intervening evidence would not have
been recorded and the audit trail would be a lie by omission. The correct sequence
is three transitions, each with its own evidence.

### Per-category rules

Different memory types need different bars. A runbook that passed once is useful;
a claim about what the project *currently* is needs more.

| Memory type | Highest state without landed history | Notes |
|---|---|---|
| **Episodic** — what happened | `verified` | A record of an event. `integrated` is meaningless for it; the event happened regardless of what landed. |
| **Semantic** — current project facts | `reviewed` | **Requires `integrated` to be presented as current truth.** Below that, served with a branch-local or unconfirmed label. This is where "verification is not enough" bites hardest. |
| **Procedural** — verified commands and runbooks | `verified` after one passing run of the exact command | `reviewed` on independent approval, `integrated` when it landed. |
| **Decision** — chosen option, rejected alternatives, rationale | `integrated` directly when `source=human` | A human decision *is* project truth by authority, not by test. `authority` is recorded. Agent-proposed decision records start `quarantined` like anything else. |
| **Gotcha / negative** — failed approaches | `verified` on a deterministic verification-failure event | May reach `reviewed` if a reviewer confirmed the finding. **Never promoted to semantic project truth** — a gotcha describes what failed, not what is. |
| **Performance** — agent and profile outcomes | `verified` | Deterministically aggregated from outcome events. Never `integrated`; a statistic does not land in a commit. |

### Cross-scope promotion

Widening a record's scope — branch → repository, repository → project, project →
global — is a promotion in its own right and needs its own evidence.

- **branch → repository** requires landed integration (`integration_state` in
  {`integrated_run`, `accepted_user`}).
- **repository → project** requires explicit human approval.
- **project → global** is **not implemented in 0.1.0.** See
  [ADR-0016](../adr/ADR-0016-global-memory-deferral.md). Cross-project leakage is
  threat T9 and the safest 0.1.0 answer is that the capability does not exist.

## 5. Every transition is auditable

No state changes without a `memory_transitions` row recording:

| Field | Meaning |
|---|---|
| `transition_id` | Identity |
| `memory_id` | Subject |
| `from_state` / `to_state` | The change |
| `policy_rule` | The **named rule** that authorised it, e.g. `promote.procedural.verified_by_command` |
| `evidence_event_ids` | The specific events relied on |
| `actor` | Who or what performed it |
| `actor_source` | Their `source` classification |
| `recorded_at` | When |
| `scope` | The scope affected |
| `note` | Optional human note |

`policy_rule` is the field that makes the model falsifiable rather than
decorative: for any record you can ask *which rule promoted this, on what
evidence*, and get a name and a list of event IDs rather than a shrug. A refused
transition is also recorded, with the rule that refused it — refusals are evidence
too, and a silently-dropped promotion attempt is exactly what an attacker wants.

## 6. What can never happen

These are invariants, asserted in `tests/security/`. A change that breaks one
should fail CI.

1. **A `rejected` record is never promoted.** Terminal, permanent, no exception.
2. **An agent never promotes.** No SDK path, CLI path, or MCP tool grants an
   `agent`-sourced actor a promotion.
3. **The MCP surface has no promotion tool.** Not disabled — absent. A test asserts
   the tool list contains no promotion, invalidation, supersession, scope-movement,
   rebuild, or import tool.
4. **Semantic project truth requires landed history.** A semantic record below
   `integrated` is never served unlabelled as current fact.
5. **Self-review never promotes.** Author and reviewer are compared; a match is
   refused and the refusal recorded.
6. **A rung is never skipped.**
7. **An unverifiable signature never grants trust.** Fail-closed, including when
   the optional verification dependency is absent.
8. **Retrieved memory is never presented as instruction.** Every digest opens with
   the untrusted-data banner.

**The single narrow exception.** An `invalidated` record may return to a ladder
state if — and only if — a later deterministic evidence event shows the fact holds
again (a reverted revert; a dependency restored). This requires fresh evidence and
a new transition row, and it is logged as a re-validation with the rule
`revalidate.invalidated.fresh_evidence`. `superseded` and `rejected` have no such
path: supersession is resolved by writing a new record, and rejection is
permanent.

## 7. How trust affects retrieval

Trust is not a filter alone — it is a weighted signal, so that a highly relevant
`verified` gotcha can outrank a marginally relevant `integrated` fact when the
gotcha is what the caller actually needs.

- `min_trust` sets a hard floor. Default: `observed`. Terminal states are excluded
  unless explicitly requested.
- `trust_weight` contributes to the score, monotonically increasing with rank.
- `verification_weight` and review/integration evidence contribute separately.
- `poisoning_risk` subtracts.
- Unresolved contradictions subtract, and raise a digest warning.
- **Semantic records below `integrated` are labelled**, so a caller can see the
  difference between "the project uses `uv`" (landed) and "this branch uses `uv`"
  (not yet).

Exact weights, defaults, and rationale: [ADR-0008](../adr/ADR-0008-retrieval-and-ranking.md)
and [`docs/reference/RETRIEVAL.md`](../reference/RETRIEVAL.md). Every component of
every score is reportable through `provalume explain`.

## 8. Reading the model out of a live database

```sh
provalume memories --trust verified --explain   # what is trusted, and why
provalume memories --trust quarantined          # what is not, and why not
provalume explain <memory-id>                   # full provenance and score breakdown
provalume audit                                 # do the transitions hold up
```

`explain` is the intended way to interrogate this model. If it cannot tell you why
a record is trusted, the record should not be trusted.
