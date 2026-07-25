# ADR-0004: Six memory categories

**Status:** Accepted · **Date:** 2026-07-25

## Context

The CoALA taxonomy (working / episodic / semantic / procedural) is the field's
standard. It is useful as a schema-design tool because each type wants a different
write policy, retention policy, and retrieval treatment — not because the names are
important.

Applied to software agents, four types leave two things homeless. **Decisions**
("we chose Typer over Click because…") are neither facts about the code nor
procedures; their authority is human, and collapsing them into semantic memory
loses the rejected alternatives, which are the useful part. **Failed approaches**
are the highest-value memory an agent can have and the one most systems discard —
they are not "episodic events" in any useful sense, because what matters is the
reusable *signature*, not the incident.

A fifth need emerged from Orkestra's existing `ledger` table: which agent profile
actually succeeds at which task category.

## Decision

**Six persistent categories, plus working memory generated at query time.**

| Category | Contents | Write trigger | Promotion ceiling without landed history |
|---|---|---|---|
| **`episodic`** | Attempts, failures, repairs, reviews, run outcomes | Deterministic projection of events | `verified` |
| **`semantic`** | Current repository facts, environment requirements, conventions, architecture, constraints | Landed integration, or human decision | `reviewed` — **needs `integrated` to be current truth** |
| **`procedural`** | Verified commands, runbooks, repair/test/release procedures | One passing verification of the *exact* command | `verified` |
| **`decision`** | Selected option, rejected alternatives, rationale, authority, consequences | A human decision event | `integrated` when `source=human` |
| **`gotcha`** | Failed approach, failure signature, context, later resolution, continued applicability | A verification-failure event | `verified` — **never promoted to semantic truth** |
| **`performance`** | Agent/profile evidence per task category: success, review approval, verification, fallback outcome | Deterministic aggregation of outcome events | `verified` |

**Working memory is not stored.** It is the bounded digest composed at query time
([ADR-0008](ADR-0008-retrieval-and-ranking.md)). Persisting it would create a
second, staler source of truth and a second thing to poison.

### Why the categories are not interchangeable

The promotion ceilings differ because the evidence that would justify them differs:

- **Semantic is the hardest** because it is the only category asserting what *is*.
  A test passing in one worktree does not change the project. Landed history does.
- **Gotcha is grounded in failure**, which means `trust_state=verified` alongside
  `verification_state=failed`. Both true; one field could not express it. A gotcha
  can never be promoted to semantic truth — it describes what failed, not what is.
- **Decision derives authority from a human**, not from a test. Human decisions are
  project truth by authority. Agent-*proposed* decisions start `quarantined` like
  anything else.
- **Performance is a statistic.** `integrated` is meaningless for it — an aggregate
  does not land in a commit.

### Shared fields

Every memory carries, regardless of category: `memory_id`, `memory_type`,
`content` (structured JSON), `text` (readable), `project_id`, `repository_id`,
`branch`, `run_id`, `task_id`, `attempt_id`, `source_event_ids`, `author_agent`,
`adapter`, `model`, `effort`, `valid_at`, `invalid_at`, `recorded_at`,
`supersedes_id`, `trust_state`, `verification_state`, `review_state`,
`integration_state`, `commit_sha`, `access_count`, `last_accessed_at`,
`content_hash`, `redaction`, `poisoning_risk`.

Both `content` and `text` exist deliberately: `content` is what policy and
retrieval filters reason over, `text` is what goes into a digest. Deriving one from
the other at read time would make ranking depend on formatting.

## Consequences

**Good.** Each category gets the write and promotion policy it actually needs.
Retrieval can weight by category — a preflight check wants gotchas, planning wants
decisions and semantic facts. Failed attempts are a first-class asset rather than
noise. `performance` gives cross-agent learning a home.

**Bad.** Six categories is more surface than four: more policy code, more tests,
more for a user to learn. Mitigated by the CLI defaulting to sensible category sets
per command rather than making the user choose.

**Also bad.** Category boundaries are occasionally genuinely ambiguous — is "the
integration suite is flaky under parallelism" a semantic fact or a gotcha? The
resolution rule: if it describes something that *failed*, it is a gotcha; if it
describes what *is*, it is semantic. Documented in
[`DATA_MODEL.md`](../reference/DATA_MODEL.md) with worked examples, because a rule
that lives only in an ADR will be applied inconsistently.

## Alternatives rejected

**Plain CoALA four types.** Loses decisions and gotchas, the two categories with
the most distinctive policy needs.

**One undifferentiated table with a free-text tag.** Tags cannot carry per-category
promotion rules. This is what most reviewed systems do, and it is why none of them
can say what a record needed in order to be trusted.

**Namespaces as strings** (as in `madebyaris/agent-orchestration`: `context` /
`decisions` / `findings` / `blockers`). The instinct is right; a closed enum with
per-category policy is the version that can be enforced.
