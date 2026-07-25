# JSONL interchange specification

SQLite is the operational store. JSONL is what crosses machines and enters Git.

Decision record: [ADR-0011](../adr/ADR-0011-jsonl-interchange.md).

---

## Layout

```
<export-dir>/
  events.jsonl        one event per line
  memories.jsonl      one memory per line
  transitions.jsonl   one lifecycle transition per line
```

**No header line.** A header would conflict on every concurrent export — exactly
the Git-merge pain this format exists to avoid. Each record self-describes with
`rv` (record version) and `kind`.

## Determinism

- Canonical JSON: sorted keys, `(",", ":")` separators, UTF-8, no NaN/Infinity
- Records sorted by `(kind, id)`
- One record per line, `\n` terminated

Exporting the same database twice produces **byte-identical files**. A test
asserts it. Because IDs are time-sortable ULIDs, new records land near the end, so
diffs stay append-mostly rather than churning.

## Record shape

```json
{"rv":1,"kind":"event","id":"01JXYZ…","event_type":"verification.failed",
 "project_id":"my-app","source":"kernel","recorded_at":"2026-07-25T14:32:01.482Z",
 "payload":{"command":"pytest -n auto","exit_code":1},
 "payload_hash":"sha256:…"}
```

Absent optional fields are **omitted**, not emitted as null: a file full of nulls
is noisier to diff, and omission is unambiguous because every optional is nullable
in the schema.

### Fields deliberately not exported

`seq`, `event_hash`, `prev_event_hash`.

These describe *this database's* local chain, not the record. Exporting them would
invite an importer to treat a foreign chain as its own — and the chain is a local
tamper-evidence mechanism, not a global ledger.

## Import rules

An imported record is **untrusted input**. Every rule below follows from that.

| Situation | Behaviour |
|---|---|
| Duplicate `event_id`, identical content | Skipped. Idempotent. |
| Duplicate `event_id`, **different** content | **Conflict.** Never overwritten — that is the forgery path. |
| Declared `payload_hash` ≠ actual | **Rejected.** The hash is recomputed, never trusted: leaving a stale hash on a tampered payload is how a forgery would pass as a duplicate. |
| `rv` newer than supported | Rejected, or quarantined with `--quarantine-unknown`. Never partially interpreted. |
| `rv` older than supported | Migrated forward if a migration exists, else rejected |
| Foreign `project_id` | Rejected unless `--allow-foreign-project` |
| Divergent supersession | Both imported, conflict recorded, **not** resolved by recency |
| Conflicting semantic facts | Both imported as a contradiction pair |
| Missing provenance | Imported, trust capped, provenance marked unresolvable |
| Invalid or unverifiable signature | **Quarantined.** Fail-closed. |
| Line over 1 MB | Line rejected and reported; the file continues |
| Malformed JSON | Line rejected and reported; the file continues |

### Imported records are never trusted on arrival

`source=import`, ceiling `observed`, landing state `quarantined`. **A record's
claimed trust state in a file carries no weight.** Trust is re-derived locally
from evidence that also imported and also validated.

Consequence worth knowing: two teammates importing each other's exports may
legitimately reach *different* trust states for the same record, because trust
depends on which evidence each of them holds. That is correct, and it surprises
people.

## Signatures

Optional, and the two schemes differ in what they prove:

| Scheme | Requires | Proves |
|---|---|---|
| `hmac-sha256` | stdlib only | The signer held the shared secret. Anyone with it can forge. |
| `ed25519` | `provalume[signatures]` | The holder of a specific private key produced it. |

Both fail closed. Without the `cryptography` extra, Ed25519-signed records are
**quarantined with an explicit reason** rather than accepted unverified. Keys must
be pinned in advance — a record carrying its own key would be self-authenticating,
which is not authentication.

> **A valid signature proves origin, never truthfulness.** A signed lie is a
> verified-origin lie.

```python
from provalume.interchange import signatures

key = b"team-shared-secret"
pv.export("./mem", signer=lambda r: signatures.sign_hmac(r, key=key, key_id="team"))

verifier = signatures.Verifier(hmac_keys={"team": key})
pv.import_records("./mem", verifier=verifier)
```

## Team workflow

0.1.0 ships **single-machine-first with team-ready semantics**: the IDs, hashing,
and merge rules support collaboration; the workflow is documented rather than
automated. No sync daemon, no conflict-resolution UI, no encrypted transport.

```sh
provalume audit --strict            # gate on secrets before anything leaves
provalume export --out ./memory
git add memory/ && git commit -m "memory: export"

git pull
provalume import ./memory
```

`export` **refuses to run** if audit finds unredacted credential patterns. Export
is the last place to catch a leak and the worst place to discover one later.

## Confidentiality

There is none. JSONL is plaintext and carries command strings, error excerpts,
branch names, and worktree paths. Transport security is your problem. Scope
filters let you export a subset.

## Versioning

`rv` is independent of the package version ([ADR-0017](../adr/ADR-0017-compatibility-and-versioning.md)).
Current: **1**.

A record from the future is rejected rather than partially interpreted, because a
record you cannot fully validate is a record you cannot safely trust — and
importing the fields you happen to recognise is how forged provenance gets in.
