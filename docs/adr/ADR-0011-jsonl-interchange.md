# ADR-0011: JSONL interchange format

**Status:** Accepted · **Date:** 2026-07-25

## Context

SQLite is the right operational store and the wrong sharing format: a binary file
conflicts irreconcilably in Git and cannot be reviewed in a diff.

Beads (25.6k stars, verified) demonstrates the working pattern — JSONL committed to
the repository, the database as a rebuildable cache, hash IDs so multiple agents and
machines merge without conflicts. The research report recommended deciding
single-machine versus team scope *before* v0, since the merge semantics are cheaper
to design early than to retrofit.

## Decision

**JSONL is the portable interchange format. SQLite stays the operational store.
Merge semantics are designed now; team workflows are documented, not automated.**

### Layout

```
<export-dir>/
  events.jsonl        one event per line
  memories.jsonl      one memory per line
  transitions.jsonl   one lifecycle transition per line
```

No header line. A header would conflict on every concurrent export — precisely the
Git-merge pain the format exists to avoid. Each record self-describes with `rv`
(record version) and `kind`.

### Determinism

- Canonical JSON: sorted keys, `(",", ":")` separators, UTF-8, no NaN/Infinity.
- Records sorted by `(kind, id)`. Since IDs are time-sortable ULIDs, new records
  append near the end, so diffs are append-mostly rather than churning.
- Exporting the same database twice produces byte-identical files. Tested.

### Record shape

```json
{"rv":1,"kind":"event","id":"01JXYZ…","event_type":"verification.completed",
 "project_id":"…","payload_hash":"sha256:…","payload":{…},"source":"kernel", …}
```

Local-only fields are omitted deliberately: `seq`, `event_hash`, and
`prev_event_hash` are properties of *this* database's chain
([ADR-0002](ADR-0002-immutable-event-journal.md)), not of the record. Exporting them
would invite an importer to treat a foreign chain as its own.

### Import rules

| Situation | Behaviour |
|---|---|
| Duplicate `event_id`, identical content | Skipped. Idempotent. |
| Duplicate `event_id`, **different** content | **Rejected** as a conflict. Never silently overwritten — this is the forgery path. |
| Unknown `rv` newer than supported | Rejected with a clear message, or quarantined with `--quarantine-unknown` |
| Unknown `rv` older than supported | Migrated forward if a migration exists, else rejected |
| Foreign `project_id` | Rejected unless `--allow-foreign-project` |
| Divergent supersession (two records superseding one predecessor) | Both imported, conflict recorded, **not** resolved by recency |
| Conflicting semantic facts | Both imported as a contradiction pair; retrieval penalises and warns |
| Missing provenance (`source_event_ids` absent locally) | Imported, trust capped, provenance marked unresolvable |
| Invalid or unverifiable signature | Quarantined. Fail-closed. |
| Line over the size cap | Rejected; the file is not aborted, the line is reported |
| Malformed JSON | Line rejected and reported; import continues |

**Only events are stored.** Memory and transition records in a file are parsed and
checked — divergent supersession is detected there — and then dropped: a memory is
a projection of events ([ADR-0002](ADR-0002-immutable-event-journal.md)), and the
importer rebuilds its own from the events it accepted. `provalume import` counts
and reports them separately for that reason.

**Imported records never arrive trusted.** `source=import`, ceiling `observed`
([ADR-0005](ADR-0005-trust-lifecycle.md)) — a record's *claimed* trust state in a
file carries no weight. Trust is re-derived locally from evidence that also imported
and also validated.

### Signatures

Optional, two schemes, with an honest difference between them:

| Scheme | Requires | Proves |
|---|---|---|
| `hmac-sha256` | stdlib only | The signer held the shared secret. Anyone with the secret can forge. |
| `ed25519` | `provalume[signatures]` | The holder of a specific private key produced it. |

Both fail closed. If `cryptography` is absent, Ed25519-signed records are
**quarantined with an explicit reason**, never accepted unverified. Keys must be
pinned; an unknown signer is an untrusted signer.

Keys are pinned by the operator, on the command line:

```sh
provalume export --out ./export --sign-hmac team=./team.key
provalume import ./export --hmac-key team=./team.key   # --require-signature to
                                                       # quarantine unsigned records
```

`--hmac-key` and `--ed25519-key` are repeatable. Pin nothing and no signature is
examined at all — verifying a record against a key that record supplied would
verify nothing.

**A valid signature proves origin, not truthfulness.** A signed lie is a
verified-origin lie. Signatures raise confidence about *who*, never about *what* —
stated here because the opposite assumption is the natural one.

### Team scope

**0.1.0 ships single-machine-first with team-ready semantics.** The IDs, hashing, and
merge rules support multi-machine collaboration; the workflow is documented
(export → commit → pull → import) rather than automated. No sync daemon, no
conflict-resolution UI, no encrypted transport.

This is the report's recommendation followed exactly: design the merge semantics
now, ship single-machine, avoid retrofitting identity later.

## Consequences

**Good.** Git-reviewable memory. Merge-friendly by construction. The database is
never the only copy. Diffs are human-readable, so a poisoned import is visible in
review. Deterministic export makes round-trip testable.

**Bad.** Verbose — JSONL of a large journal is much larger than the database.
Export is a full serialisation, not incremental, in 0.1.0.

**Bad.** Import cannot fully reconstruct trust, because trust depends on local
evidence. Two teammates importing each other's exports may legitimately reach
different trust states for the same record. Correct — trust is local — and
surprising, so it is documented in [`JSONL.md`](../reference/JSONL.md).

**Also bad.** No confidentiality. JSONL is plaintext; exports carry command strings,
error excerpts, branch names, and worktree paths. Transport security is the
operator's problem, and `export` refuses to run if `audit` finds unredacted
credential patterns.

## Alternatives rejected

**Ship the SQLite file.** Binary, unmergeable, unreviewable.

**One JSONL file for everything.** Mixed record types make per-kind sorting and
selective import awkward, and every export touches one file.

**A header line with metadata.** Conflicts on every concurrent export.

**Sort by insertion order rather than ID.** Two machines produce different orderings
for the same content, so exports are not comparable.

**Automatic bidirectional sync.** Requires conflict resolution Provalume cannot do
safely — auto-resolving a semantic contradiction is auto-deciding which of two
agents was right.
