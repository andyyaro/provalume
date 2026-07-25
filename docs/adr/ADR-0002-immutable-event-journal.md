# ADR-0002: Immutable event journal as the source of truth

**Status:** Accepted · **Date:** 2026-07-25

## Context

Provalume's claim is that a record was proved by specific evidence. That claim
needs the evidence to still be there, unedited, and to be reconstructible.

A mutable `memories` table cannot do this. If promotion overwrites a row, the
question "what evidence promoted this, and when?" has no answer. If a fact is
updated in place, "what did we believe at commit X?" is unanswerable. Both
questions are the product.

## Decision

**Events are the source of truth. Everything else is a rebuildable projection.**

Projections: memory records, the FTS index, transitions, contradiction links,
vectors, digests. All derivable from the journal, all droppable and rebuildable
with `provalume rebuild`.

### Envelope

Every event carries, where applicable: `event_id`, `seq`, `schema_version`,
`event_type`, `recorded_at`, `occurred_at`, `project_id`, `repository_id`,
`run_id`, `task_id`, `attempt_id`, `agent_profile`, `adapter`, `model`, `effort`,
`branch`, `worktree`, `base_commit`, `commit_sha`, `causal_parent_event_id`,
`source`, `payload`, `payload_hash`, `event_hash`, `prev_event_hash`, `redaction`,
`integrity`.

`project_id`, `event_type`, `source`, `recorded_at`, and `schema_version` are
mandatory. The rest are optional because not every event has a branch or an
attempt, and inventing one would be a lie.

### Append-only, enforced by the database

SQLite triggers `RAISE(ABORT)` on `UPDATE` and `DELETE` against `events`.
Application-level discipline is not enough: the threat model includes someone
opening the file with `sqlite3`, and a trigger stops the casual case that a code
convention does not.

### Hashing, and what the chain does and does not prove

- `payload_hash` = SHA-256 of the canonical JSON payload. **Globally stable** —
  the same payload hashes identically on any machine, which is what makes
  cross-machine duplicate detection possible.
- `event_hash` = SHA-256 over the canonical envelope including `payload_hash` and
  `prev_event_hash`. **Locally chained** — tamper-evident within one database.

The chain is deliberately *local*. Events imported from elsewhere are appended in
arrival order and chained into the receiving database's sequence; the chain is not
a global ledger and Provalume does not pretend it is. Global identity comes from
`event_id` (a time-sortable ULID-style identifier, collision-resistant across
machines) and `payload_hash`.

`provalume audit` recomputes the chain and reports the first divergence, plus the
current head so it can be pinned externally against rollback (threat T16).

**This is detection, not prevention.** A local attacker who can write the file can
recompute the chain. Said plainly in [`THREAT_MODEL.md`](../security/THREAT_MODEL.md) §7.

### Canonical JSON

One serialisation, used for hashing, export, and comparison: UTF-8, sorted keys,
`(",", ":")` separators, no NaN or Infinity, integers not floats where integral,
RFC 3339 UTC timestamps with explicit precision. Determinism is not cosmetic here —
a hash that varies by dict ordering makes every downstream claim unverifiable.

### Idempotent ingestion

`INSERT … ON CONFLICT(event_id) DO NOTHING`. Re-ingesting the same `event_id` with
*different* content is an error, detected by comparing `event_hash` — silently
accepting it would let an importer rewrite history through the append-only path.

### Redaction before the durable write

`validate → size caps → redact → poisoning scan → hash → write`. Hashing happens
after redaction, so hashes are over what is actually stored. A post-hoc redaction
pass would mean the secret hit the disk first.

## Consequences

**Good.** Full auditability. Time-travel queries. Corruption in a projection is
recoverable rather than fatal. Schema evolution can re-derive old events under new
rules. A memory system whose promotion decisions can be re-run is a memory system
whose promotion decisions can be trusted.

**Bad.** The database grows monotonically; nothing is ever reclaimed by normal
operation. Two writes per logical change (event + projection). Rebuild time scales
with journal size — measured and reported by `provalume audit`. Bugs in projection
logic require a rebuild rather than a row fix.

**Also bad.** Hard deletion is not supported. This makes Provalume a poor fit for
data under a deletion requirement, stated in
[`PRIVACY_MODEL.md`](../security/PRIVACY_MODEL.md) §6.

## Alternatives rejected

**Mutable memory table with an audit log alongside.** Two sources of truth that
drift. The audit log becomes decorative the first time someone fixes a row directly.

**Content-addressed `event_id`.** Two genuinely distinct events with identical
content (the same command failing twice) would collide. Rejected in favour of ULID
identity plus content hashing for dedup detection.

**Global hash chain across machines.** Requires coordination Provalume does not
have, for a guarantee it cannot enforce locally. Local chain plus optional
signatures ([ADR-0011](ADR-0011-jsonl-interchange.md)) is the honest scope.
