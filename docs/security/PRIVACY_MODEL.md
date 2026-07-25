# Provalume privacy model

The short version:

> **Provalume makes no network connections. It has no telemetry, no analytics, no
> crash reporting, no update check, and no account. Nothing leaves your machine
> unless you run an export or point an optional embedder at a model you installed
> yourself.**

The rest of this document is the detail behind that, because a privacy claim
without specifics is a slogan.

---

## 1. Where data lives

| Path | Contents | Committed to Git? |
|---|---|---|
| `.provalume/provalume.db` | The SQLite database: events, memories, transitions, FTS index, optional vectors | **No.** `.provalume/` is in `.gitignore`. |
| `.provalume/provalume.db-wal`, `-shm` | SQLite write-ahead log and shared-memory file | No |
| `.provalume/config.toml` | Local configuration, if created | No |
| Wherever you point `provalume export` | JSONL interchange files | **Your choice** — see §4 |

The database is **project-local by default**. There is no `~/.provalume/`, no
global store, and no cross-project memory in 0.1.0
([ADR-0016](../adr/ADR-0016-global-memory-deferral.md)). Two projects on the same
machine cannot see each other's memory because they are different files.

Deleting `.provalume/` deletes everything Provalume knows. There is no second copy.

## 2. What is recorded

Being specific matters more than being reassuring, because some of these fields are
sensitive and you should know before you run it.

### Fields that can contain sensitive data

| Field | Contains | Why it is recorded |
|---|---|---|
| `payload` | Structured event content: command strings, exit codes, error output excerpts, reviewer findings, file paths | This *is* the evidence. Without it, "verified by command X" is unfalsifiable. |
| `text` | Human-readable memory text, derived from payloads | What gets shown to agents. |
| `worktree` | Absolute path to a worktree | Distinguishes concurrent contradictory worktrees (eval scenario 6). |
| `branch`, `base_commit`, `commit_sha` | Git identifiers | Branch-aware truth is a core feature. |
| `agent_profile`, `adapter`, `model`, `effort` | Which agent did what | Performance memory and independent-review checks. |
| `project_id`, `repository_id` | Project identity | Scope isolation (threat T9). |

**Command strings and error output are the sensitive ones.** A failing command's
output routinely contains credentials, internal hostnames, customer data in test
fixtures, and file paths that reveal directory structure. Redaction (§3) is what
stands between that and durable storage.

### What is never recorded

- Source code file *contents*. Provalume stores paths and error excerpts, not files.
- Full command output. Excerpts are bounded by size caps at admission.
- Anything about the machine beyond what a caller passes in: no hostname, no
  username, no MAC address, no OS fingerprint, no CPU/memory details, no locale,
  no timezone beyond UTC timestamps.
- Any identifier that would correlate this installation with another.

## 3. Redaction

Redaction runs **before the durable write**, on the structured payload. It is not a
post-hoc pass over stored rows, because a post-hoc pass means the secret was on
disk first.

```
input → validate → size caps → REDACT → poisoning scan → hash → write
```

Rule families, applied in order:

| Family | Covers |
|---|---|
| Provider-prefixed keys | `sk-`, `sk-ant-`, `ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_`, `github_pat_`, `glpat-`, `AIza`, `AKIA`/`ASIA`, `xox[baprs]-`, `xapp-`, `npm_`, `pypi-` |
| Structured credentials | `aws_secret_access_key`, `aws_session_token`, Azure `AccountKey=`/`SharedAccessSignature=`, SAS `sig=`, `privateKeyData` |
| Generic assignments | any key ending in `password`, `passwd`, `secret`, `api_key`/`apikey`, `token`, `credential`, `auth`, optionally JSON-quoted, with a value that is not an obvious placeholder or path |
| Tokens and keys | JWTs (`eyJ…`), PEM private-key blocks, `Authorization: Bearer …` |
| URL userinfo | `scheme://user:secret@host` → user and host kept, secret replaced |
| `.npmrc` style | `_authToken=…` |

Every rule favours recall over precision. **A redacted false positive is an
annoyance; a persisted credential is an incident.** You will occasionally see
`[REDACTED]` where nothing secret was — that is the intended trade.

Redaction metadata (whether rules fired, which families, how many matches) is
recorded alongside the record, so you can tell "clean" apart from "cleaned".

### Verifying it worked

```sh
provalume audit          # rescans stored content for known credential patterns
provalume audit --strict # non-zero exit on any finding — use in CI
```

`audit` finding nothing is not proof of no secrets — it proves no *known pattern*
is present. A credential with no recognisable shape (a bare password, an internal
API key with no prefix) may survive. **If you know a specific secret transited a
run, treat the database as containing it and rotate.** Do not treat a clean audit
as clearance.

## 4. What can leave the machine

Exactly two paths, both explicit.

### 4.1 `provalume export`

The only way Provalume writes data outside `.provalume/`. It is always an explicit
command; nothing exports on a schedule or on shutdown.

Export contains events, memories, and transitions — including command strings,
error excerpts, branch names, worktree paths, and agent identifiers. Redaction runs
again on the way out as defence in depth, and export **refuses to run** if audit
finds unredacted credential patterns.

Before committing an export to a shared repository:

```sh
provalume audit --strict           # gate on secrets
provalume export --out ./mem       # then inspect it
grep -riE 'token|secret|password|/Users/|/home/' ./mem   # eyeball it
```

Scope filters (`--project`, `--repository`, `--branch`, `--exclude-scope`) let you
export a subset. Absolute worktree paths reveal local directory structure; use
`--redact-paths` to replace them with stable opaque identifiers if you are sharing
outside your own machine.

### 4.2 Optional embedders

Vector retrieval is off by default and not required for anything. When enabled:

- `model2vec` and `fastembed` run **locally on CPU**. They need network access
  **once**, to download the model you asked for, and never again.
- The built-in `HashingEmbedder` is stdlib-only, needs no network ever, and is a
  deterministic non-semantic baseline for testing — not a quality embedder.
- **No hosted embedding API is supported.** There is no code path that sends text
  to a remote embedding service, and no API-key configuration for one.
- Embeddings are computed from **already-redacted stored text** (threat T12). There
  is no path from raw input to an embedder.

If you never install a vectors extra, Provalume has no code that opens a socket.

## 5. Verifying the no-network claim yourself

Do not take it on trust:

```sh
# No network-capable library is a required dependency
pip show provalume | grep Requires        # pydantic, typer, rich

# No socket, urllib, http, or requests usage in the source
grep -rnE '\b(socket|urllib|httpx|requests|http\.client|aiohttp)\b' \
  "$(python -c 'import provalume,pathlib;print(pathlib.Path(provalume.__file__).parent)')"

# Or watch it: run the demo under a network monitor
provalume demo
```

The grep is expected to return nothing. `tests/security/test_no_network.py`
asserts this in CI, so a dependency that introduces network access fails the build.

## 6. Retention and deletion

Provalume does not expire data on its own. It grows until you act.

| To do this | Run |
|---|---|
| Remove everything | `rm -rf .provalume/` |
| Neutralise a record without losing the audit trail | `provalume invalidate <id> --reason "…"` |
| Replace a fact, keeping history | `provalume supersede <old-id> --with <new-id>` |
| Rebuild projections from the journal | `provalume rebuild` |

**On deletion, honestly:** the event journal is append-only and enforced by
database triggers. `invalidate` and `supersede` mark records without removing them —
that is the point of a provenance system, and it means **Provalume is a poor fit
for data subject to a hard deletion requirement.** If a secret or personal datum
entered the journal, the reliable remedies are to delete the database, or to export
with filters and re-import into a fresh one. There is no surgical redaction of an
already-written event, by design.

If you need deletion guarantees for regulated data, do not put that data through an
agent whose outputs Provalume records.

## 7. Multi-user and shared hosts

There is **no access control**. No authentication, no authorisation, no per-user
isolation, no notion of a memory another user may not read. Anyone who can read
`.provalume/provalume.db` can read everything; anyone who can write it can tamper
with it (detectably — see [`THREAT_MODEL.md`](THREAT_MODEL.md) T14).

Concretely: do not place a Provalume database on a shared host and expect isolation
between users. Use filesystem permissions, and full-disk encryption if the content
warrants it.

The MCP server has a `--read-only` mode and project scoping, which limit what a
*client* can do. They are not a substitute for filesystem permissions.

## 8. Summary table

| Question | Answer |
|---|---|
| Does it phone home? | No. No telemetry, analytics, crash reporting, update checks, or accounts. |
| Does it need an API key? | No. None, at any tier. |
| Does it need an LLM? | No. Not for writes, not for retrieval, not for anything. |
| Does it need network? | No — unless you opt into an embedder extra, which downloads a model once. |
| Where is my data? | `.provalume/provalume.db`, in your project. Nowhere else. |
| Is it committed to Git? | No. `.provalume/` is gitignored. |
| Can it leak between projects? | No. Separate files; `project_id` filtered on every query. |
| Can it leak between branches? | No. Branch is a first-class scope with enforced filtering. |
| Are secrets redacted? | Before the durable write, with an auditable rescan. Known patterns only. |
| Can I check for secrets? | `provalume audit --strict` |
| Can I delete everything? | `rm -rf .provalume/` |
| Can I delete one thing surgically? | No — invalidate or supersede. The journal is append-only by design. |
| Is there access control? | No. Single-operator. Use filesystem permissions. |
