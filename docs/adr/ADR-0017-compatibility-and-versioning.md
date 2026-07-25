# ADR-0017: Compatibility and versioning

**Status:** Accepted · **Date:** 2026-07-25

## Context

Provalume has four independently-versioned surfaces, and conflating them would make
every change look breaking:

1. The Python SDK, imported by other code.
2. The database schema, which persists across upgrades.
3. The JSONL interchange format, which crosses machines and versions.
4. The event schema, which is embedded in every stored record.

A database written by 0.1.0 must still be readable by 0.4.0, and a JSONL file
exported by a teammate on a newer version must fail loudly rather than import
half-understood.

## Decision

**Semantic versioning for the package, with independent integer versions for the
schema, the interchange format, and the event schema.**

### Package version

Standard semver. Pre-1.0, so `0.x` minor bumps may break the SDK — stated plainly
rather than implied. The rule from 1.0 onward: breaking SDK changes need a major
bump.

`provalume.__version__` is the single source of truth, read from installed package
metadata so the CLI, the MCP `serverInfo`, and exports cannot disagree with the
wheel.

### Database schema version

A monotonic integer in `schema_version`. Linear, forward-only migrations
([ADR-0003](ADR-0003-sqlite-wal-and-migrations.md)).

| Situation | Behaviour |
|---|---|
| Database version < code | Migrate forward automatically on open, in one transaction per migration |
| Database version = code | Open |
| Database version > code | **Refuse to open**, with a message naming both versions and saying to upgrade |

Refusing is not unhelpfulness — operating on a schema whose semantics you do not
know is how data gets corrupted quietly.

### Event schema version

`schema_version` on every event record. Old events are readable forever: writers
emit the current version, readers accept every version they know, and a projection
rebuild applies **the rules appropriate to each event's own version**. That last
point is what lets promotion logic evolve without retroactively changing what old
evidence meant.

### JSONL record version

`rv` on every record ([ADR-0011](ADR-0011-jsonl-interchange.md)).

| Situation | Behaviour |
|---|---|
| `rv` < supported | Migrated forward on import if a migration exists, else rejected with the reason |
| `rv` = supported | Imported |
| `rv` > supported | **Rejected**, or quarantined with `--quarantine-unknown` |

Never partially interpreted. A record from the future is a record you cannot
validate, and importing the fields you happen to recognise is how forged provenance
gets in.

### What is public API

Stability is a promise, so its boundary is explicit.

**Public and stable within a minor series:**

- `provalume.Provalume` (the SDK client) and its documented methods
- The `provalume.schemas` public models
- The `Embedder` protocol
- CLI command names, their documented options, and their `--json` output shapes
- The MCP tool names and their input/output schemas
- The JSONL format at a given `rv`

**Private, changeable without notice:** everything under `provalume.store`,
`provalume.policy`, `provalume.retrieval`, `provalume.writers`, `provalume.interchange`,
and `provalume.mcp` internals. Importing them is unsupported.

The convention: a leading-underscore name is private, and a module not re-exported
from `provalume/__init__.py` is internal regardless of its name.

### `--json` output is a contract

Every CLI command supporting `--json` emits a stable, documented shape. This is
what integrations parse, so changing a key is a breaking change even though no
Python signature moved. Asserted in `tests/compatibility/test_json_output.py`.

### Deprecation

Deprecated surfaces emit a `DeprecationWarning`, are documented in
[`CHANGELOG.md`](../../CHANGELOG.md), and survive at least one minor release before
removal.

### Compatibility tests

`tests/compatibility/` holds fixture databases and JSONL files from each released
schema version, and asserts they still open, migrate, and import. Fixtures are
committed, so a migration that breaks an old database fails CI rather than a user's
upgrade.

## Consequences

**Good.** Databases survive upgrades. Old events remain interpretable under their
own rules. Foreign-version files fail loudly. The public/private line is stated, so
"you broke my code" has a checkable answer.

**Bad.** Four version numbers is more to track and to explain. Mitigated by
`provalume doctor` printing all four.

**Bad.** Forward-only migrations mean downgrading requires a backup. Documented in
[`MIGRATION.md`](../reference/MIGRATION.md), and it is the safer default: a
down-migration that has to un-invent a supersession chain cannot be written
correctly.

**Also bad.** Keeping fixture databases for every version grows the repository
slowly. Small text-sized files; worth it.

## Alternatives rejected

**One version number for everything.** A schema addition would force a package
major bump, or a package change would imply a schema change. Both mislead.

**Date-based schema versions.** Reads nicely, sorts badly across timezones, and
makes "is this newer?" a parsing question.

**Best-effort import of unknown `rv`.** Silently interpreting a subset of a future
record is how forged provenance and half-understood trust states arrive.

**No public/private distinction.** Every internal refactor becomes a support
question.
