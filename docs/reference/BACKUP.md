# Backup, restore, and migration

## What to back up

One file: `.provalume/provalume.db`.

That is everything. There is no second copy, no cache to warm, and no server
state. Deleting `.provalume/` deletes everything Provalume knows.

## Backing up safely

SQLite in WAL mode keeps recent writes in a sidecar file, so copying only the
`.db` while a writer is active can capture a torn state.

```sh
# Correct: SQLite's own backup, safe with a writer running
sqlite3 .provalume/provalume.db ".backup /path/to/backup.db"

# Also correct: stop the writer first, then copy all three files
cp .provalume/provalume.db .provalume/provalume.db-wal .provalume/provalume.db-shm /backup/
```

Verify what you took:

```sh
provalume audit --db /path/to/backup.db
```

If that passes, the backup is internally consistent — chain intact, projections
matching, pragmas as expected, no known credential patterns.

## Restoring

```sh
cp /path/to/backup.db .provalume/provalume.db
provalume audit
provalume rebuild --check      # bring projections up to the journal
```

`rebuild` is safe to run at any time: it drops projections and reconstructs them
from the journal, which is the only authoritative thing in the file.

## The portable alternative

A database is one file and one format. JSONL survives both:

```sh
provalume audit --strict          # gate on secrets before anything leaves
provalume export --out ./memory-backup
```

Plain text, Git-diffable, and readable by any tool. `export` refuses to run if
audit finds unredacted credential patterns.

Restoring into a fresh database:

```sh
provalume init
provalume import ./memory-backup
provalume rebuild
```

**Trust does not survive this round trip intact.** Imported records arrive
`quarantined` and their trust is re-derived from evidence that also imported.
That is correct — trust is local — and it surprises people, so plan for it. If
you need trust states preserved exactly, back up the database file.

## Upgrading Provalume

Migrations run automatically on open, forward-only, one transaction per
migration. A crash mid-migration leaves a consistent earlier version.

```sh
uv tool upgrade provalume
provalume doctor         # confirms the schema version
provalume audit
```

## Downgrading

Not supported. A database migrated forward will be **refused** by an older build
rather than opened optimistically:

```
database schema version 3 is newer than this build of Provalume supports (2).
Upgrade Provalume; operating on an unknown schema would corrupt data.
```

Refusing is the safe behaviour. Down-migrations that have to un-invent a
supersession chain cannot be written correctly, which is why there are none.

To downgrade: restore a backup taken before the upgrade, or export from the new
version and import into a fresh database on the old one — accepting the trust
reset described above.

## Moving between machines

The database file is portable: same schema, same code, copy it. Vector blobs are
stored little-endian explicitly so a copied file reads correctly on either
endianness.

Note that `worktree` paths and `repository_id` are recorded as they were on the
original machine. Retrieval degrades gracefully — commit validity falls back to
`uncertain` where a repository is absent — but paths in the record will refer to
the old layout.

## If something is wrong

```sh
provalume audit                  # what specifically is wrong
provalume rebuild                # rebuild every projection from the journal
provalume audit                  # confirm
```

`rebuild` fixes projection-level corruption. It cannot fix a damaged journal —
that is what backups are for.

A `sqlite integrity_check` failure means file-level damage. Restore from backup;
there is nothing to salvage in place.

## Retention

Provalume does not expire anything. The journal is append-only and superseded
records are retained by design.

If a database has grown past what you want to keep, the supported approach is to
export the range you want and import it into a fresh database. There is no
compaction command, because "compaction" of a provenance journal is deletion of
evidence, and it should be a deliberate act rather than a scheduled one.
