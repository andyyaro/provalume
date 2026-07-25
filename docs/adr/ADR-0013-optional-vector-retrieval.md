# ADR-0013: Optional vector retrieval

**Status:** Accepted · **Date:** 2026-07-25

## Context

Lexical retrieval misses synonyms. A query for "dependency resolution failure" will
not match a record phrased "package solver conflict". That is the genuine cost of
[ADR-0008](ADR-0008-retrieval-and-ranking.md)'s no-embeddings default.

Adding embeddings by default would cost more than it buys: `fastembed` pulls
onnxruntime; the agentmemory trial installed **1.0 GB** with vendored ONNX runtimes
for three platforms. A memory library that installs a machine-learning runtime by
default has misjudged its own weight class. And `sqlite-vec` is pre-1.0 with a small
maintainer base — a hard dependency on it is a Kuzu-shaped risk.

## Decision

**Vector retrieval is optional, off by default, marked experimental, and can never
authorise a record.**

### Embedder protocol

```python
class Embedder(Protocol):
    model_id: str      # versioned, recorded with every vector
    dimensions: int
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...
```

Three implementations:

| Embedder | Requires | Purpose |
|---|---|---|
| `HashingEmbedder` | **nothing** — stdlib only | Deterministic hashing-trick projection. A **non-semantic baseline for testing**, not a quality embedder. Makes the entire vector code path testable in CI with zero dependencies. |
| `Model2VecEmbedder` | `provalume[model2vec]` | Static embeddings, MIT, CPU, ~30 MB, no torch in the base install |
| `FastEmbedEmbedder` | `provalume[fastembed]` | ONNX models; heavier |

`HashingEmbedder` exists so that fusion, fallback, rebuild, and the
vector-cannot-bypass-filters guarantees are exercised by tests on every commit
rather than only when an optional extra is installed. It is labelled as a baseline
everywhere it appears, because a hashing projection has no semantic meaning and
reporting a retrieval score from it as a quality result would be dishonest.

**No hosted embedding API is supported.** No API-key configuration, no code path
that sends text to a remote service.

### Storage

`memory_vectors(memory_id, model_id, dimensions, vector BLOB)` — float32,
little-endian, fixed width.

Search uses `sqlite-vec` where it loads safely, otherwise brute-force cosine in
numpy, otherwise the vector path is unavailable and retrieval is lexical-only.
Extension loading is capability-checked at runtime, because some CPython builds
lack `enable_load_extension`. `provalume doctor` reports which path is active.

`model_id` is stored per vector. Mixing embedders in one index produces meaningless
distances, so a model change invalidates the index and requires
`provalume rebuild --vectors`.

### Fusion

Reciprocal rank fusion:

```
rrf_score = Σ  1 / (k + rank_i)      k = 60
```

`k = 60` is the value from Cormack et al. (2009), the paper RRF comes from. Named
rather than tuned, so it is not an undocumented magic number.

RRF over ranks, not scores, deliberately: BM25 and cosine are not on comparable
scales, and normalising them against each other would invent a comparison.

### The hard constraint

**Vectors reorder an already-authorised candidate set. They never authorise a
record.**

Every vector result passes, afterwards, through the identical filters as a lexical
result: `project_id`, trust floor, scope applicability, commit validity,
invalidation, terminal-state exclusion, poisoning threshold, redaction. There is one
filter implementation and both paths call it — not two implementations that must be
kept in step.

That is what makes threat T6 (poisoned vector index) survivable: an adversarial
embedding can win a similarity contest and still not be returned.

### Benchmarking, and what will not be claimed

The eval harness (scenario 20) compares four configurations on the same fixtures:
lexical only · vector only · hybrid · hybrid with governance scoring.

**No superiority claim will be published unless it is reproducible from the
committed harness.** Any comparison is Provalume against Provalume, on Provalume's
own fixtures, and says so. Given the field's record of corrected headline numbers,
this is the only defensible posture.

## Consequences

**Good.** Default install stays at three pure-Python dependencies. Synonym recall is
available to users who want it. The entire vector path is CI-tested without any
optional extra. A poisoned index cannot bypass governance.

**Bad.** Two retrieval paths to maintain and test. Mitigated by a single shared
filter implementation and by `HashingEmbedder` keeping both paths exercised.

**Bad.** `sqlite-vec` is pre-1.0. Pinned, optional, and never on a required path.
If it is abandoned, the numpy fallback continues working and nothing breaks.

**Also bad.** Users who enable vectors and expect a large improvement may be
disappointed. The literature suggests hybrid gains over well-tuned lexical are real
but modest, and Provalume will not oversell them. Marked experimental in 0.1.0.

## Alternatives rejected

**Vectors by default.** Would install an ML runtime for a memory library, and would
make embeddings the retrieval gate — threat T6.

**Vectors only.** Loses exact-match precision, which for commands, file paths, and
error strings is exactly what matters.

**Require `sqlite-vec`.** Pre-1.0 with a small maintainer base, on a required path.
The Kuzu archival is the cautionary case.

**Score-normalised fusion instead of RRF.** BM25 and cosine are not comparable
scales; RRF is rank-based precisely to avoid inventing that comparison.

**Ship no `HashingEmbedder`.** Would leave the vector path untested unless an
optional extra is installed, which is how optional code paths rot.
