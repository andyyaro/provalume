"""The append-only event journal.

Everything Provalume claims rests on this file. Events go in; nothing comes out
except by reading. Appends are idempotent, hashed, and chained.

Two hash properties, and the distinction matters (ADR-0002):

``payload_hash``
    Globally stable. The same payload hashes identically on any machine, which is
    what makes cross-machine duplicate detection work.

``event_hash`` / ``prev_event_hash``
    A chain local to *this* database. Tamper-evident here; deliberately not a
    global ledger. Imported events are appended in arrival order and chained into
    the receiving database's own sequence.

Re-ingesting the same ``event_id`` with different content is an **error**, not an
overwrite. Silently accepting it would let an importer rewrite history through the
one path that is supposed to be append-only.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from typing import Any

from provalume import _time
from provalume.errors import IntegrityError, StoreError
from provalume.interchange.hashing import hash_envelope, hash_payload
from provalume.schemas.events import Event, EventFilter, EventType
from provalume.schemas.trust import Source
from provalume.store.db import Database

_EVENT_COLUMNS = (
    "event_id",
    "schema_version",
    "event_type",
    "recorded_at",
    "occurred_at",
    "project_id",
    "repository_id",
    "run_id",
    "task_id",
    "attempt_id",
    "agent_profile",
    "adapter",
    "model",
    "effort",
    "branch",
    "worktree",
    "base_commit",
    "commit_sha",
    "causal_parent_event_id",
    "source",
    "payload",
    "payload_hash",
    "event_hash",
    "prev_event_hash",
    "redaction",
    "integrity",
)

# Built from _EVENT_COLUMNS, a module constant. Every value is bound; no caller
# input reaches the SQL text.
_INSERT_SQL = (
    f"INSERT INTO events ({', '.join(_EVENT_COLUMNS)}) "  # noqa: S608  # nosec B608
    f"VALUES ({', '.join('?' for _ in _EVENT_COLUMNS)})"
)

_SELECT_SQL = f"SELECT seq, {', '.join(_EVENT_COLUMNS)} FROM events"  # noqa: S608  # nosec B608


class AppendResult:
    """What an append did.

    ``inserted=False`` with no error means the event was already present with
    identical content — idempotent re-ingestion, which import relies on.
    """

    __slots__ = ("event", "inserted", "seq")

    def __init__(self, *, event: Event, inserted: bool, seq: int) -> None:
        self.event = event
        self.inserted = inserted
        self.seq = seq

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        verb = "inserted" if self.inserted else "duplicate"
        return f"<AppendResult {verb} {self.event.event_id} seq={self.seq}>"


class Journal:
    """Append-only access to the event table."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # -- head --------------------------------------------------------------

    def head(self) -> tuple[str, int, int]:
        """``(event_hash, seq, event_count)`` of the chain head.

        Exposed so an operator can pin it externally — in a commit, or a signed
        export — which is the only real defence against a rollback attack (T16).
        Provalume makes that possible; it does not do it for you.
        """
        row = self.db.query_one(
            "SELECT event_hash, seq, event_count FROM journal_head WHERE id = 1"
        )
        if row is None:  # pragma: no cover - migration guarantees the row
            return "", 0, 0
        return str(row["event_hash"]), int(row["seq"]), int(row["event_count"])

    # -- append ------------------------------------------------------------

    def append(self, event: Event) -> AppendResult:
        """Append one event, computing its hashes and extending the chain.

        The caller is expected to have already run admission (validation, size
        caps, redaction, poisoning scan). This method hashes what it is given, so
        skipping admission would mean hashing an unredacted payload.
        """
        payload_hash = hash_payload(event.payload)

        existing = self.db.query_one(
            f"{_SELECT_SQL} WHERE event_id = ?", (event.event_id,)
        )
        if existing is not None:
            stored = _row_to_event(existing)
            if stored.payload_hash != payload_hash:
                msg = (
                    f"event {event.event_id} already exists with different content "
                    f"(stored payload hash {stored.payload_hash}, incoming {payload_hash}). "
                    "Refusing: an append-only journal cannot be rewritten."
                )
                raise IntegrityError(msg)
            return AppendResult(event=stored, inserted=False, seq=int(existing["seq"]))

        with self.db.tx() as conn:
            prev_hash, prev_seq, count = self.head()
            envelope = event.envelope()
            event_hash = hash_envelope(
                envelope, payload_hash=payload_hash, prev_event_hash=prev_hash
            )
            stamped = event.model_copy(
                update={
                    "payload_hash": payload_hash,
                    "event_hash": event_hash,
                    "prev_event_hash": prev_hash,
                }
            )
            try:
                cursor = conn.execute(_INSERT_SQL, _event_to_row(stamped))
            except sqlite3.IntegrityError as exc:  # pragma: no cover - raced insert
                msg = f"failed to append event {event.event_id}: {exc}"
                raise StoreError(msg) from exc
            seq = int(cursor.lastrowid or (prev_seq + 1))
            conn.execute(
                "UPDATE journal_head SET event_hash = ?, seq = ?, event_count = ? WHERE id = 1",
                (event_hash, seq, count + 1),
            )
            conn.execute(
                "INSERT OR IGNORE INTO projects (project_id, created_at) VALUES (?, ?)",
                (stamped.project_id, stamped.recorded_at),
            )

        return AppendResult(
            event=stamped.model_copy(update={"seq": seq}), inserted=True, seq=seq
        )

    def append_many(self, events: list[Event]) -> list[AppendResult]:
        """Append a batch in one transaction.

        All-or-nothing: a partially-applied import would leave a chain whose head
        does not correspond to any coherent set of events.
        """
        results: list[AppendResult] = []
        with self.db.tx():
            for event in events:
                results.append(self.append(event))
        return results

    # -- read --------------------------------------------------------------

    def get(self, event_id: str) -> Event | None:
        row = self.db.query_one(
            f"{_SELECT_SQL} WHERE event_id = ?", (event_id,)
        )
        return None if row is None else _row_to_event(row)

    def get_many(self, event_ids: tuple[str, ...]) -> list[Event]:
        if not event_ids:
            return []
        placeholders = ", ".join("?" for _ in event_ids)
        rows = self.db.query(
            f"{_SELECT_SQL} WHERE event_id IN ({placeholders}) ORDER BY seq",
            tuple(event_ids),
        )
        return [_row_to_event(r) for r in rows]

    def find(self, spec: EventFilter) -> list[Event]:
        """Structured query. No caller-supplied SQL crosses this boundary."""
        clauses: list[str] = []
        params: list[Any] = []

        if spec.project_id is not None:
            clauses.append("project_id = ?")
            params.append(spec.project_id)
        if spec.event_types:
            placeholders = ", ".join("?" for _ in spec.event_types)
            clauses.append(f"event_type IN ({placeholders})")
            params.extend(t.value for t in spec.event_types)
        if spec.sources:
            placeholders = ", ".join("?" for _ in spec.sources)
            clauses.append(f"source IN ({placeholders})")
            params.extend(s.value for s in spec.sources)
        for field in ("run_id", "task_id", "attempt_id", "branch", "commit_sha"):
            value = getattr(spec, field)
            if value is not None:
                clauses.append(f"{field} = ?")
                params.append(value)
        if spec.since is not None:
            clauses.append("recorded_at >= ?")
            params.append(_time.normalize(spec.since))
        if spec.until is not None:
            clauses.append("recorded_at <= ?")
            params.append(_time.normalize(spec.until))

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        order = "ASC" if spec.ascending else "DESC"
        sql = f"{_SELECT_SQL}{where} ORDER BY seq {order} LIMIT ? OFFSET ?"
        params.extend([spec.limit, spec.offset])
        return [_row_to_event(r) for r in self.db.query(sql, tuple(params))]

    def iter_all(self, *, project_id: str | None = None, batch: int = 1000) -> Iterator[Event]:
        """Stream every event in chain order.

        Used by rebuild and export, where the journal may be far larger than
        memory. Paginates on ``seq`` rather than OFFSET so cost stays linear.
        """
        last_seq = 0
        while True:
            if project_id is None:
                sql = f"{_SELECT_SQL} WHERE seq > ? ORDER BY seq LIMIT ?"
                params: tuple[Any, ...] = (last_seq, batch)
            else:
                sql = (
                    f"{_SELECT_SQL} WHERE seq > ? AND project_id = ? "
                    "ORDER BY seq LIMIT ?"
                )
                params = (last_seq, project_id, batch)
            rows = self.db.query(sql, params)
            if not rows:
                return
            for row in rows:
                last_seq = int(row["seq"])
                yield _row_to_event(row)

    def count(self, *, project_id: str | None = None) -> int:
        if project_id is None:
            return int(self.db.scalar("SELECT COUNT(*) FROM events") or 0)
        return int(
            self.db.scalar("SELECT COUNT(*) FROM events WHERE project_id = ?", (project_id,))
            or 0
        )

    def latest_seq(self) -> int:
        return int(self.db.scalar("SELECT COALESCE(MAX(seq), 0) FROM events") or 0)

    # -- integrity ---------------------------------------------------------

    def verify_chain(self, *, limit: int | None = None) -> list[str]:
        """Recompute the whole chain, returning descriptions of any divergence.

        Empty means the chain is internally consistent. This is **detection, not
        prevention**: an attacker with write access to the file can recompute the
        chain after editing it. Recording the head somewhere Provalume does not
        control is what turns detection into a real guarantee (T14, T16).
        """
        problems: list[str] = []
        prev_hash = ""
        checked = 0  # stays 0 when the journal is empty; enumerate rebinds it

        for checked, event in enumerate(self.iter_all(), start=1):
            expected_payload = hash_payload(event.payload)
            if event.payload_hash != expected_payload:
                problems.append(
                    f"event {event.event_id} (seq {event.seq}): payload hash mismatch "
                    f"— stored {event.payload_hash}, recomputed {expected_payload}"
                )
            if event.prev_event_hash != prev_hash:
                problems.append(
                    f"event {event.event_id} (seq {event.seq}): chain break — "
                    f"prev_event_hash is {event.prev_event_hash or '(empty)'}, "
                    f"expected {prev_hash or '(empty)'}"
                )
            expected_event_hash = hash_envelope(
                event.envelope(),
                payload_hash=event.payload_hash,
                prev_event_hash=event.prev_event_hash,
            )
            if event.event_hash != expected_event_hash:
                problems.append(
                    f"event {event.event_id} (seq {event.seq}): envelope hash mismatch "
                    f"— stored {event.event_hash}, recomputed {expected_event_hash}"
                )
            prev_hash = event.event_hash
            if limit is not None and checked >= limit:
                return problems

        stored_head, stored_seq, stored_count = self.head()
        actual_count = self.count()
        if stored_count != actual_count:
            problems.append(
                f"journal_head records {stored_count} events but the table holds "
                f"{actual_count} — rows were removed or inserted out of band"
            )
        if actual_count and stored_head != prev_hash:
            problems.append(
                f"journal_head hash {stored_head} does not match the recomputed "
                f"chain head {prev_hash}"
            )
        if stored_seq != self.latest_seq():
            problems.append(
                f"journal_head seq {stored_seq} does not match max(seq) {self.latest_seq()}"
            )
        return problems

    def duplicate_payloads(self, *, project_id: str | None = None) -> list[tuple[str, int]]:
        """``(payload_hash, count)`` for payloads appearing more than once.

        Not an error — a command genuinely failing twice produces identical
        payloads. Surfaced because it is also the signature of a re-imported
        export, which is worth noticing.
        """
        if project_id is None:
            sql = (
                "SELECT payload_hash, COUNT(*) AS n FROM events "
                "GROUP BY payload_hash HAVING n > 1 ORDER BY n DESC"
            )
            params: tuple[Any, ...] = ()
        else:
            sql = (
                "SELECT payload_hash, COUNT(*) AS n FROM events WHERE project_id = ? "
                "GROUP BY payload_hash HAVING n > 1 ORDER BY n DESC"
            )
            params = (project_id,)
        return [(str(r["payload_hash"]), int(r["n"])) for r in self.db.query(sql, params)]


def _event_to_row(event: Event) -> tuple[Any, ...]:
    return (
        event.event_id,
        event.schema_version,
        event.event_type.value,
        event.recorded_at,
        event.occurred_at,
        event.project_id,
        event.repository_id,
        event.run_id,
        event.task_id,
        event.attempt_id,
        event.agent_profile,
        event.adapter,
        event.model,
        event.effort,
        event.branch,
        event.worktree,
        event.base_commit,
        event.commit_sha,
        event.causal_parent_event_id,
        event.source.value,
        json.dumps(event.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        event.payload_hash,
        event.event_hash,
        event.prev_event_hash,
        json.dumps(event.redaction, sort_keys=True, separators=(",", ":")),
        json.dumps(event.integrity, sort_keys=True, separators=(",", ":")),
    )


def _row_to_event(row: sqlite3.Row) -> Event:
    keys = row.keys()
    return Event(
        event_id=row["event_id"],
        schema_version=int(row["schema_version"]),
        event_type=EventType(row["event_type"]),
        recorded_at=row["recorded_at"],
        occurred_at=row["occurred_at"],
        project_id=row["project_id"],
        repository_id=row["repository_id"],
        run_id=row["run_id"],
        task_id=row["task_id"],
        attempt_id=row["attempt_id"],
        agent_profile=row["agent_profile"],
        adapter=row["adapter"],
        model=row["model"],
        effort=row["effort"],
        branch=row["branch"],
        worktree=row["worktree"],
        base_commit=row["base_commit"],
        commit_sha=row["commit_sha"],
        causal_parent_event_id=row["causal_parent_event_id"],
        source=Source(row["source"]),
        payload=json.loads(row["payload"]),
        payload_hash=row["payload_hash"],
        event_hash=row["event_hash"],
        prev_event_hash=row["prev_event_hash"],
        redaction=json.loads(row["redaction"]),
        integrity=json.loads(row["integrity"]),
        seq=int(row["seq"]) if "seq" in keys else None,
    )
