# Migration guide

Provalume versions four things independently, because conflating them would make
every change look breaking ([ADR-0017](../adr/ADR-0017-compatibility-and-versioning.md)).

| Surface | Versioning | Current |
|---|---|---|
| Package | semantic versioning | 0.1.0 |
| Database schema | monotonic integer | 1 |
| Event schema | integer, stored per event | 1 |
| JSONL record | integer `rv`, per record | 1 |

`provalume doctor` prints all four.

---

## Upgrading

```sh
uv tool upgrade provalume
provalume doctor
provalume audit
```

Schema migrations run automatically when the database is opened: linear,
forward-only, one transaction per migration together with the version bump. A
crash mid-migration leaves a consistent earlier version rather than a half-applied
one.

Migrations may rebuild projections. They never destroy journal rows.

## A database newer than the code

Refused, not opened optimistically:

```
database schema version 3 is newer than this build of Provalume supports (2).
Upgrade Provalume; operating on an unknown schema would corrupt data.
```

Upgrade Provalume, or use a backup taken before the other machine upgraded.

## Downgrading

Not supported. There are no down-migrations, and there will not be: a
down-migration that has to un-invent a supersession chain cannot be written
correctly.

Options, in order of preference:

1. Restore a backup from before the upgrade.
2. Export from the new version, import into a fresh database on the old one —
   accepting that imported records arrive `quarantined` and re-derive trust
   locally.

## Pre-1.0 SDK stability

`0.x` minor bumps **may** break the SDK. Said plainly rather than implied. From
1.0, breaking SDK changes need a major bump.

Deprecated surfaces emit a `DeprecationWarning`, appear in `CHANGELOG.md`, and
survive at least one minor release before removal.

## What is public

**Stable within a minor series:**

- `provalume.Provalume` and its documented methods
- the `provalume.schemas` public models
- the `Embedder` protocol
- CLI command names, documented options, and `--json` output shapes
- MCP tool names and their input/output schemas
- the JSONL format at a given `rv`

**Internal, changeable without notice:** everything under `provalume.store`,
`provalume.policy`, `provalume.retrieval`, `provalume.writers`,
`provalume.interchange`, and the `provalume.mcp` internals.

The convention: a leading-underscore name is private, and a module not
re-exported from `provalume/__init__.py` is internal regardless of its name.

### `--json` output is a contract

Integrations parse it, so changing a key is a breaking change even though no
Python signature moved. `tests/compatibility/` asserts the shapes.

## Old events stay interpretable

Every event stores the `schema_version` current when it was recorded. A rebuild
applies the rules appropriate to **each event's own version**, so promotion logic
can evolve without retroactively changing what old evidence meant.

Readers accept every version they know; writers emit the current one.

## JSONL across versions

| Situation | Behaviour |
|---|---|
| `rv` older than supported | Migrated forward if a migration exists, else rejected |
| `rv` equal | Imported |
| `rv` newer | **Rejected**, or quarantined with `--quarantine-unknown` |

Never partially interpreted. A record from the future is one you cannot validate,
and importing the fields you happen to recognise is how forged provenance gets in.

## Compatibility testing

`tests/compatibility/` holds fixture databases and JSONL files from each released
schema version and asserts they still open, migrate, and import. Fixtures are
committed, so a migration that breaks an old database fails CI rather than a
user's upgrade.

## Before upgrading in anything that matters

```sh
sqlite3 .provalume/provalume.db ".backup /backup/pre-upgrade.db"
provalume audit --strict
uv tool upgrade provalume
provalume doctor
provalume audit --strict
provalume rebuild --check
```

See [`BACKUP.md`](BACKUP.md).
