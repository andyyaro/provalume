# Architecture overview

## The dependency direction

Each layer depends only on those above it. That ordering is the architecture; the
rest is detail.

```
schemas and policies          types, trust rules, promotion rules
        ↓
immutable event journal       the source of truth (append-only)
        ↓
deterministic projections     events → memory records
        ↓
memory lifecycle              promotion, invalidation, supersession
        ↓
retrieval and composition     filter → score → explain → digest
        ↓
SDK / CLI / MCP               the interfaces
        ↓
integrations                  the only layer that may know about a host
```

**The core never imports a host.** `provalume.integrations.orkestra` translates
another system's records into Provalume events and imports nothing from it — a
test asserts this, because "extract it later" is exactly how hidden couplings form
([ADR-0014](../adr/ADR-0014-orkestra-integration-boundary.md)).

## The two things worth understanding

### 1. Events are the source of truth

Memory records, the FTS index, transitions, contradiction links, and vectors are
all **projections**. Drop them and `provalume rebuild` reconstructs them from the
journal, byte for byte.

That is testable rather than aspirational: eval scenario 13 rebuilds and compares
content hashes. If a rebuild produced different records, the journal would not
really be the source of truth.

Consequences that constrain the projector: no wall-clock time (every timestamp
comes from an event) and no dict-order dependence (iteration is over sorted keys).

### 2. Filters authorise; scoring only reorders

```
candidates ──▶ HARD FILTERS ──▶ authorised set ──▶ SCORING ──▶ ranked results
               project_id                          relevance
               trust floor                         trust
               terminal exclusion                  evidence
               validity                            recency
               scope                                usage
               commit validity                     − penalties
```

Nothing scored can appear that filtering did not admit, and no score can promote a
record past a filter. This is what makes an adversarial embedding survivable: it
can win the similarity contest and still not be returned.

## Module map

| Module | Responsibility |
|---|---|
| `schemas/` | Types: events, memories, trust, scope, provenance, retrieval. No behaviour beyond validation. |
| `store/db.py` | SQLite connection, pragmas, linear migrations |
| `store/journal.py` | Append-only events, hash chain, idempotent ingestion |
| `store/repository.py` | Memory, transition, link, contradiction, signature storage |
| `store/projections.py` | Events → memories; applies promotion decisions |
| `store/fts.py` | FTS5 query construction and safety |
| `store/gitinfo.py` | Read-only Git: ancestry, commit existence, branch |
| `store/integrity.py` | `audit` — chain, projections, pragmas, credentials, provenance |
| `policy/admission.py` | Boundary 1: validate → cap → redact → scan → hash |
| `policy/promotion.py` | Boundary 2: the only place trust is granted |
| `policy/invalidation.py` | Withdrawal, supersession, contradiction detection |
| `policy/poisoning.py` | Poisoning heuristics (Tier 2 of the defence) |
| `policy/scope.py` | Scope widening rules; path confinement |
| `writers/` | Deterministic event → memory functions, one module per source |
| `retrieval/lexical.py` | The engine: filter, score, explain |
| `retrieval/ranking.py` | The scoring formula and deterministic ordering |
| `retrieval/digest.py` | Boundary 3: budgeted, banner-first working memory |
| `retrieval/preflight.py` | The pre-action warning gate |
| `retrieval/vectors.py` | Optional embedders, RRF fusion, vector index |
| `interchange/hashing.py` | Canonical JSON and deterministic hashing |
| `interchange/jsonl.py` | Export and import |
| `interchange/signatures.py` | HMAC and Ed25519, fail-closed |
| `sdk/client.py` | The public Python API |
| `cli/main.py` | The `provalume` command |
| `mcp/` | Stdlib MCP stdio server and its permission model |
| `redact.py` | Secret redaction, before every durable write |

## Storage

One SQLite file: `.provalume/provalume.db`. WAL, foreign keys on, busy timeout,
`trusted_schema` off. No ORM.

| Table | Contents |
|---|---|
| `events` | The journal. Append-only, enforced by triggers. |
| `journal_head` | Chain head, so appending needs no scan and rollback is detectable |
| `memories` | Projections. Mutable, rebuildable. |
| `memory_transitions` | Every lifecycle change, **including refusals** |
| `memories_fts` | External-content FTS5 index over `memories` |
| `failure_signatures` | Signature → occurrence count → resolution |
| `contradictions` | Detected conflicts, never auto-resolved |
| `memory_links` | Gotcha ↔ resolution, supersession edges |
| `memory_vectors` | Optional embeddings, per model |
| `projection_state` | How far projections have caught up |

## The write path

```
caller
  │
  ├─▶ admission        validate → size caps → REDACT → poisoning scan
  │                    (redaction before hashing, so hashes cover what is stored)
  ├─▶ journal.append   payload hash, envelope hash, chain link, idempotent insert
  │
  └─▶ projector.apply  writers produce candidates
                       promotion.can_promote decides, one rung at a time
                       every decision recorded as a transition
```

## The read path

```
RecallQuery
  │
  ├─▶ candidates       FTS5 MATCH + bm25, or structured filter when no query text
  ├─▶ hard filters     project, trust floor, terminal, validity, scope, commit
  ├─▶ scoring          nine components, each recorded
  ├─▶ ordering         (−score, −recorded_at, memory_id) — fully deterministic
  ├─▶ explanations     reasons, filters passed, breakdown, warnings
  └─▶ digest           banner first, hard budget, per-item trust labels
```

## What has no place here

- **A language model.** Not in the write path, not in the read path, not in
  ranking ([ADR-0007](../adr/ADR-0007-deterministic-writers.md)).
- **A network call.** No telemetry, no update check, no hosted anything —
  asserted by `tests/security/test_no_network.py`.
- **A required heavy dependency.** Three pure-Python runtime dependencies;
  everything else is an optional extra.
- **A write to your repository.** Git access is strictly read-only.

## Further reading

[ADRs](../adr/) record each decision and what it cost.
[`TRUST_MODEL.md`](../security/TRUST_MODEL.md) specifies the lifecycle.
[`THREAT_MODEL.md`](../security/THREAT_MODEL.md) enumerates 26 threats and their
controls.
