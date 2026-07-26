# Performance

Figures from the eval harness on a 2026-era Apple Silicon laptop, in-memory
databases, single writer. Reproduce with `provalume eval --json`.

**These are small-corpus figures.** They tell you the engine is not accidentally
quadratic; they do not tell you how it behaves at a million events, because that
has not been measured.

---

## Measured

| Operation | Median | p95 |
|---|---:|---:|
| Retrieval (FTS + scoring + explanations) | ~1.7 ms | ~1.7 ms |
| Write (admission + journal + projection) | ~3.8 ms | ~3.8 ms |
| Rebuild (small corpus) | ~2.4 ms | — |
| `record_verification` with a git repository (write + radius extraction) | ~50 ms | — |

The last row is the freshness axis's cost (ADR-0020): in a git-backed
project, recording a verification also extracts a blast radius and appends a
radius event per claim record. The overhead is git subprocesses (read-only
plumbing; the verification command is never executed), extraction runs once
per verification, and per-instance caches cover the repeated questions.
Git-less clients skip all of it and pay the bare write cost.

Latency is long-tailed in principle, so the harness reports median and p95 rather
than a mean — a mean hides exactly the slow queries a user would notice.

## Where the time goes

**A write** does more than an insert: validation, size caps, redaction over the
structured payload, a poisoning scan, canonical serialisation, two hashes, the
journal insert, then projection — which may fold a gotcha, run promotion rules,
and record transitions. ~4 ms for all of that is the cost of the guarantees.

**A read** runs an FTS5 MATCH with `bm25()`, caps the candidate set at 500,
applies the hard filters, scores nine components per candidate, builds
explanations, and sorts deterministically.

## What scales well

- **FTS5 lookup** is indexed and does not degrade with journal size.
- **Candidate scoring** is bounded by `candidate_cap` (default 500), so a query
  against a large corpus costs the same as one against a small corpus once the
  cap binds.
- **Appending** is O(1): the chain head is a single row, so no `MAX()` scan.
- **`iter_all`** paginates on `seq` rather than `OFFSET`, keeping streaming
  linear.

## What does not

**Rebuild is O(journal).** It replays every event. Fine at thousands, and it will
become noticeable at millions. It is not on any hot path — only `provalume
rebuild` and `--check`.

**`audit --deep` is O(journal + memories).** It re-scans stored content for
credential patterns. Use `audit` without `--deep` for a fast structural check.

**The database only grows.** Append-only journal, superseded records retained.
Text-sized records, so growth is modest, but there is no compaction and there
will not be — compacting a provenance journal is deleting evidence.

**Git ancestry costs subprocess calls.** Cached per `GitInfo` instance and only
consulted for candidates that survived the other filters, but a query touching
many distinct commits pays for it.

## Tuning

```python
from provalume import Provalume, RankingPolicy

pv = Provalume.open(policy=RankingPolicy(candidate_cap=100))   # faster, less recall
```

| Knob | Effect |
|---|---|
| `candidate_cap` | The main lever. Fewer candidates scored, lower recall. |
| `limit` on a query | Does not reduce scoring work — the cap does |
| `char_budget` on a digest | Affects rendering only, not retrieval |
| Vectors | Adds an embedding pass per query; the numpy path is much faster than pure Python |

## Concurrency

Provalume is **single-writer** ([ADR-0003](../adr/ADR-0003-sqlite-wal-and-migrations.md)).
WAL means readers never block the writer. Concurrent writers serialise on a
5-second busy timeout rather than failing.

`tests/integration/test_concurrency_and_recovery.py` verifies that concurrent
writers do not corrupt the chain — not that they are fast.

## Durability

`synchronous=NORMAL` with WAL: durable across **process** crashes, which is the
failure mode that matters for a rebuildable cache over an append-only journal.
`FULL` would add an fsync per commit to also survive OS crashes; the trade was
judged not worth it.

A hard-kill test asserts that committed events and the hash chain survive
`SIGKILL`-equivalent termination.

## Install footprint

| | |
|---|---|
| Runtime dependencies | 3 (pydantic, typer, rich) |
| Installed packages | 13, including transitive |
| Optional extras | `signatures` (cryptography), `vectors` (numpy), `model2vec`, `fastembed` |

For comparison, and as the reason this matters: two widely-used memory servers
reviewed during design install 41 MB / 24 packages and 1.0 GB / 185 packages
respectively ([`COMPETITOR_TRIALS.md`](../research/COMPETITOR_TRIALS.md)).

## What has not been measured

- Behaviour at 10⁵–10⁶ events
- Retrieval quality at scale (see [`BENCHMARKS.md`](BENCHMARKS.md))
- Vector search on a real embedder over a real corpus
- Memory usage under sustained load

If you run Provalume at a size that makes any of these interesting, the numbers
would be genuinely useful in an issue.
