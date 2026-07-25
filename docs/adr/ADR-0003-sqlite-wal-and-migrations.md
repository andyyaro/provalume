# ADR-0003: SQLite with WAL, no ORM, linear migrations

**Status:** Accepted · **Date:** 2026-07-25

## Context

Provalume needs durable local storage with full-text search, transactions, and
concurrent reads while an orchestrator writes. It must install with no service to
run, no daemon, no container, and no configuration.

Every heavier option in the category has a cost: Postgres needs a service, Neo4j is
GPLv3, FalkorDB is SSPL, Kuzu was archived in October 2025 and stranded its
downstreams. The one substrate with none of those problems ships inside Python.

## Decision

**Standard-library `sqlite3`, one file, WAL mode, no ORM, linear forward-only
migrations.**

### Pragmas, and why each one

| Pragma | Value | Reason |
|---|---|---|
| `journal_mode` | `WAL` | Readers do not block the writer. An orchestrator writes while agents read. |
| `foreign_keys` | `ON` | Provenance links must not dangle. |
| `busy_timeout` | `5000` ms | Concurrent access retries instead of failing immediately. |
| `synchronous` | `NORMAL` | With WAL, durable across process crashes — the failure mode that matters. `FULL` costs an fsync per commit to also survive OS crashes; not worth it for a rebuildable cache over an append-only journal. |
| `trusted_schema` | `OFF` | Defence against a tampered schema executing something on open. |
| `foreign_keys` check at open | — | Verified by `doctor` and `audit`. |

Pragmas are asserted by `provalume audit`, so a database opened by other tooling
with different settings is detectable.

### No ORM

Hand-written SQL with bound parameters. The reasons are specific, not stylistic:
FTS5 and its `bm25()` ranking function are not well served by ORM abstractions; the
append-only trigger discipline is easier to reason about in plain DDL; and it keeps
mandatory dependencies at three. Every value is bound — no caller-controlled value
is ever interpolated (threat T23).

### Migrations

An ordered list of SQL scripts. `schema_version` holds an integer. Applying
migration *n* and bumping the version happen in one transaction, so a crash
mid-migration leaves a consistent earlier version.

Rules:

- **Forward-only.** No down-migrations. A rollback is: restore a backup, or export
  and re-import. Down-migrations are a well-known source of data loss and Provalume
  has an append-only journal that makes them near-impossible to write correctly.
- **A database newer than the code is refused**, with a message saying to upgrade.
  Silently operating on a schema you do not understand corrupts data.
- **Idempotent at the boundaries** — re-running the chain on a current database is
  a no-op.
- **Migrations never destroy journal rows.** A migration may rebuild projections.

### FTS5

FTS5 is required for retrieval and is compiled into essentially every CPython
build. `provalume doctor` checks for it at startup and reports clearly if missing
rather than failing at first query.

## Consequences

**Good.** Zero-install storage. One file to back up, copy, or delete. Transactional
integrity, `PRAGMA integrity_check`, and battle-tested durability. No license
encumbrance and no maintainer-abandonment risk that matters — SQLite outlives its
dependents.

**Bad.** Single-writer. Provalume assumes one writing process; concurrent writers
serialise on the busy timeout, and the SDK documents single-writer discipline.
No network access — a shared team database is not possible, which is what JSONL
interchange ([ADR-0011](ADR-0011-jsonl-interchange.md)) exists to answer.
Hand-written SQL is more code than an ORM and its correctness rests on tests.

**Also bad.** Very large journals will eventually make rebuild slow. Measured, not
assumed: figures in [`PERFORMANCE.md`](../reference/PERFORMANCE.md).

## Alternatives rejected

**Postgres.** A service to run. Contradicts zero-install. Reasonable at team scale,
which 0.1.0 does not target.

**An embedded graph database.** Kuzu's archival is the argument. Provalume's graph
needs are a handful of edges; a SQLite edge table with a recursive CTE covers them.

**Duckdb.** Excellent analytics, weaker fit for transactional append-only writes
with FTS.

**JSON files only.** No transactions, no indexing, no ranking. JSONL is the
*interchange* layer, deliberately not the operational store.
