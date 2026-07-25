"""Optional vector retrieval (ADR-0013). Experimental in 0.1.0.

**Vectors reorder an already-authorised candidate set. They never authorise a
record.** Every vector result passes through the identical governance filters as
a lexical result, using the same implementation rather than a parallel copy that
could drift. That is what makes threat T6 survivable: an adversarial embedding can
win a similarity contest and still not be returned.

Three embedders, and the first one matters more than it looks:

``HashingEmbedder``
    Standard library only, deterministic, **non-semantic**. It exists so the
    whole vector path — fusion, fallback, rebuild, and the cannot-bypass-filters
    guarantee — is exercised by CI on every commit rather than only when someone
    installs an optional extra. Optional code paths that are only tested
    optionally are how optional code paths rot. It is a *baseline*, not a quality
    embedder, and every surface says so.

``Model2VecEmbedder`` / ``FastEmbedEmbedder``
    Real semantic embeddings, opt-in, CPU-local, no API key, no network after the
    model is downloaded once.
"""

from __future__ import annotations

import contextlib
import hashlib
import math
import struct
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable

if TYPE_CHECKING:
    from provalume.store.db import Database

from provalume.errors import EmbedderUnavailable

#: Reciprocal rank fusion constant, from Cormack et al. (2009). Named rather than
#: tuned so it is not an undocumented magic number.
RRF_K: Final = 60

#: Dimensionality of the built-in baseline. Small enough to be cheap, large
#: enough that hash collisions do not dominate.
HASHING_DIMENSIONS: Final = 256


@runtime_checkable
class Embedder(Protocol):
    """Anything that can turn text into vectors.

    Public API and stable within a minor series (ADR-0017), so a user can supply
    their own without waiting for Provalume to add it.
    """

    model_id: str
    dimensions: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch. Must be deterministic for the same model and input."""


class HashingEmbedder:
    """Deterministic hashing-trick projection. A test baseline, not semantics.

    Each token is hashed to a dimension and a sign, and the resulting sparse
    vector is L2-normalised. It captures exact token overlap and nothing else —
    no synonymy, no word order, no meaning. Two texts about the same concept in
    different words score near zero.

    Its value is that it is deterministic, needs nothing, and makes the entire
    vector code path testable. Never present its scores as a retrieval-quality
    result.
    """

    def __init__(self, *, dimensions: int = HASHING_DIMENSIONS) -> None:
        if dimensions < 8:
            msg = "dimensions must be at least 8"
            raise ValueError(msg)
        self.dimensions = dimensions
        self.model_id = f"hashing-baseline-v1-d{dimensions}"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in text.lower().split():
            cleaned = token.strip(".,;:!?()[]{}\"'`")
            if not cleaned:
                continue
            # blake2b with a fixed digest size: stable across processes and
            # platforms, unlike Python's salted built-in hash().
            digest = hashlib.blake2b(cleaned.encode("utf-8"), digest_size=8).digest()
            value = struct.unpack("<Q", digest)[0]
            index = value % self.dimensions
            sign = 1.0 if (value >> 63) & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0.0:
            return vector
        return [v / norm for v in vector]


class Model2VecEmbedder:
    """Static embeddings via ``model2vec`` (MIT, CPU, no torch in the base install)."""

    def __init__(self, model_name: str = "minishlab/potion-base-8M") -> None:
        try:
            from model2vec import StaticModel
        except ImportError as exc:
            msg = (
                "model2vec is not installed. "
                "Install with: pip install 'provalume[model2vec]'"
            )
            raise EmbedderUnavailable(msg) from exc
        self._model = StaticModel.from_pretrained(model_name)
        self.model_id = f"model2vec:{model_name}"
        self.dimensions = int(self._model.dim)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [list(map(float, row)) for row in self._model.encode(list(texts))]


class FastEmbedEmbedder:
    """ONNX embeddings via ``fastembed`` (Apache-2.0). Heavier."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            msg = (
                "fastembed is not installed. "
                "Install with: pip install 'provalume[fastembed]'"
            )
            raise EmbedderUnavailable(msg) from exc
        self._model = TextEmbedding(model_name=model_name)
        self.model_id = f"fastembed:{model_name}"
        probe = next(iter(self._model.embed(["dimension probe"])))
        self.dimensions = len(probe)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [list(map(float, row)) for row in self._model.embed(list(texts))]


# --- Storage ---------------------------------------------------------------


def pack(vector: Sequence[float]) -> bytes:
    """Pack a vector as little-endian float32.

    Explicit byte order because the blob may cross machines via a copied database
    file, and native order would silently misread on the other endianness.
    """
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack(blob: bytes, dimensions: int) -> list[float]:
    expected = dimensions * 4
    if len(blob) != expected:
        msg = f"vector blob is {len(blob)} bytes, expected {expected} for {dimensions} dims"
        raise ValueError(msg)
    return list(struct.unpack(f"<{dimensions}f", blob))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity. Returns 0.0 for a zero vector rather than dividing by it."""
    if len(a) != len(b):
        msg = f"dimension mismatch: {len(a)} vs {len(b)}"
        raise ValueError(msg)
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


# --- Fusion ----------------------------------------------------------------


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]], *, k: int = RRF_K
) -> list[tuple[str, float]]:
    """Fuse ranked ID lists by reciprocal rank.

    Over *ranks*, not scores, deliberately: BM25 and cosine live on incomparable
    scales, and normalising them against each other would invent a comparison
    that does not exist. RRF only needs each list's internal ordering.

    Ties break on the identifier, so fusion is deterministic — required for the
    eval harness to produce comparable runs.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for position, identifier in enumerate(ranking, start=1):
            scores[identifier] = scores.get(identifier, 0.0) + 1.0 / (k + position)
    return sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))


class VectorIndex:
    """Vector storage and search over the ``memory_vectors`` table.

    Uses numpy when available for a fast path and falls back to pure Python
    otherwise. Both paths produce the same ordering; only speed differs.
    """

    def __init__(self, db: Database, embedder: Embedder) -> None:
        self.db = db
        self.embedder = embedder

    def _numpy(self) -> Any:
        try:
            import numpy
        except ImportError:
            return None
        return numpy

    def upsert(self, memory_id: str, text: str) -> None:
        """Embed and store one memory's vector.

        Embeds the **stored, already-redacted** text. There is no code path from
        raw input to an embedder, which is what keeps secrets out of the index
        (threat T12).
        """
        vector = self.embedder.embed([text])[0]
        with self.db.tx() as conn:
            conn.execute(
                "INSERT INTO memory_vectors (memory_id, model_id, dimensions, vector) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(memory_id, model_id) DO UPDATE SET "
                "dimensions = excluded.dimensions, vector = excluded.vector",
                (memory_id, self.embedder.model_id, self.embedder.dimensions, pack(vector)),
            )

    def upsert_many(self, items: Sequence[tuple[str, str]]) -> int:
        if not items:
            return 0
        vectors = self.embedder.embed([text for _, text in items])
        rows = [
            (mid, self.embedder.model_id, self.embedder.dimensions, pack(vec))
            for (mid, _), vec in zip(items, vectors, strict=True)
        ]
        with self.db.tx() as conn:
            conn.executemany(
                "INSERT INTO memory_vectors (memory_id, model_id, dimensions, vector) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(memory_id, model_id) DO UPDATE SET "
                "dimensions = excluded.dimensions, vector = excluded.vector",
                rows,
            )
        return len(rows)

    def search(
        self, query: str, *, limit: int = 50, candidate_ids: Sequence[str] | None = None
    ) -> list[tuple[str, float]]:
        """Rank memory IDs by similarity to ``query``.

        ``candidate_ids`` restricts the search to an already-authorised set. When
        the caller passes it, no unauthorised record can appear at all — the
        governance filter is applied *before* similarity rather than after, so
        there is nothing for an adversarial embedding to win.
        """
        rows = self._load(candidate_ids)
        if not rows:
            return []

        query_vector = self.embedder.embed([query])[0]
        numpy = self._numpy()

        if numpy is not None:
            matrix = numpy.frombuffer(
                b"".join(blob for _, blob in rows), dtype="<f4"
            ).reshape(len(rows), self.embedder.dimensions)
            vector = numpy.asarray(query_vector, dtype="<f4")
            norms = numpy.linalg.norm(matrix, axis=1)
            qnorm = float(numpy.linalg.norm(vector))
            if qnorm == 0.0:
                return []
            # Guard against zero-norm rows rather than emitting NaN, which would
            # sort unpredictably and break determinism.
            safe = numpy.where(norms == 0.0, 1.0, norms)
            sims = (matrix @ vector) / (safe * qnorm)
            sims = numpy.where(norms == 0.0, 0.0, sims)
            scored = [(rows[i][0], float(sims[i])) for i in range(len(rows))]
        else:
            scored = [
                (mid, cosine(query_vector, unpack(blob, self.embedder.dimensions)))
                for mid, blob in rows
            ]

        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return scored[:limit]

    def _load(self, candidate_ids: Sequence[str] | None) -> list[tuple[str, bytes]]:
        if candidate_ids is not None:
            if not candidate_ids:
                return []
            placeholders = ", ".join("?" for _ in candidate_ids)
            # `placeholders` is a run of "?" sized from len(candidate_ids); every
            # value below is bound, so no caller text reaches the SQL string.
            rows = self.db.query(
                "SELECT memory_id, vector FROM memory_vectors "  # noqa: S608  # nosec B608
                f"WHERE model_id = ? AND memory_id IN ({placeholders}) "
                "ORDER BY memory_id",
                (self.embedder.model_id, *candidate_ids),
            )
        else:
            rows = self.db.query(
                "SELECT memory_id, vector FROM memory_vectors WHERE model_id = ? "
                "ORDER BY memory_id",
                (self.embedder.model_id,),
            )
        return [(str(r["memory_id"]), bytes(r["vector"])) for r in rows]

    def count(self) -> int:
        return int(
            self.db.scalar(
                "SELECT COUNT(*) FROM memory_vectors WHERE model_id = ?",
                (self.embedder.model_id,),
            )
            or 0
        )

    def clear(self) -> None:
        """Drop this model's vectors.

        Needed when the embedder changes: distances from two different models are
        not comparable, so a mixed index produces meaningless rankings.
        """
        with self.db.tx() as conn:
            conn.execute(
                "DELETE FROM memory_vectors WHERE model_id = ?", (self.embedder.model_id,)
            )


def sqlite_vec_available(db: Database) -> bool:
    """Whether ``sqlite-vec`` can be loaded here.

    Checked at runtime rather than assumed: some CPython builds ship without
    ``enable_load_extension``, so a hard dependency would fail at first query on
    an otherwise working installation. When unavailable, the numpy or pure-Python
    path handles search and nothing breaks.
    """
    try:
        import sqlite_vec  # noqa: F401
    except ImportError:
        return False
    connection = getattr(db, "connection", None)
    if connection is None or not hasattr(connection, "enable_load_extension"):
        return False
    try:
        connection.enable_load_extension(True)
    except (AttributeError, Exception):
        return False
    finally:
        # Best-effort restore; the probe already told us what we needed.
        with contextlib.suppress(Exception):
            connection.enable_load_extension(False)
    return True
