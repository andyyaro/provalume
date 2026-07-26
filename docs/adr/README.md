# Architecture decision records

Each ADR records one decision, the context that forced it, and what it costs.
They were written before the corresponding implementation, not after it.

A decision here is binding until superseded by a later ADR. If the code disagrees
with an accepted ADR, that is a bug in one of them — say which in the issue.

| ADR | Decision | Status |
|---|---|---|
| [0001](ADR-0001-identity-and-scope.md) | Provalume identity and scope | Accepted |
| [0002](ADR-0002-immutable-event-journal.md) | Immutable event journal as the source of truth | Accepted |
| [0003](ADR-0003-sqlite-wal-and-migrations.md) | SQLite with WAL, no ORM, linear migrations | Accepted |
| [0004](ADR-0004-memory-taxonomy.md) | Six memory categories | Accepted |
| [0005](ADR-0005-trust-lifecycle.md) | Eight trust states, ladder plus terminal | Accepted |
| [0006](ADR-0006-branch-and-commit-semantics.md) | Branch and commit validity semantics | Accepted |
| [0007](ADR-0007-deterministic-writers.md) | Deterministic writers, no LLM in the write path | Accepted |
| [0008](ADR-0008-retrieval-and-ranking.md) | Retrieval and ranking policy | Accepted |
| [0009](ADR-0009-invalidation-and-supersession.md) | Invalidation and supersession, never overwrite | Accepted |
| [0010](ADR-0010-memory-poisoning-controls.md) | Memory poisoning controls | Accepted |
| [0011](ADR-0011-jsonl-interchange.md) | JSONL interchange format | Accepted |
| [0012](ADR-0012-mcp-permissions.md) | MCP server and permission model | Accepted |
| [0013](ADR-0013-optional-vector-retrieval.md) | Optional vector retrieval | Accepted |
| [0014](ADR-0014-orkestra-integration-boundary.md) | Orkestra integration boundary | Accepted |
| [0015](ADR-0015-worktree-materialization.md) | Worktree context-file materialization | Accepted |
| [0016](ADR-0016-global-memory-deferral.md) | Global cross-project memory deferred | Accepted |
| [0017](ADR-0017-compatibility-and-versioning.md) | Compatibility and versioning | Accepted |
| [0018](ADR-0018-visual-identity-and-design-tokens.md) | Visual identity and design tokens | Accepted |
| [0019](ADR-0019-trajectory-benchmark.md) | Trajectory benchmark | Accepted |
