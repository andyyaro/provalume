"""Memory, transition, link, and signature storage.

Memories are projections, so unlike events they may be updated and deleted — that
is what makes ``provalume rebuild`` possible. What may *not* happen is a memory
changing state without a transition row recording why; that pairing is enforced by
:meth:`MemoryRepository.transition`, which writes both in one transaction.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from typing import Any

from provalume import _ids, _time
from provalume.schemas.memories import Memory, MemoryFilter, MemoryType, Transition
from provalume.schemas.scope import Scope, ScopeLevel
from provalume.schemas.trust import (
    TERMINAL_STATES,
    IntegrationState,
    ReviewState,
    Source,
    TrustState,
    VerificationState,
    rank,
)
from provalume.store.db import Database

_MEMORY_COLUMNS = (
    "memory_id",
    "schema_version",
    "memory_type",
    "content",
    "text",
    "scope_level",
    "project_id",
    "repository_id",
    "branch",
    "run_id",
    "task_id",
    "attempt_id",
    "source_event_ids",
    "source",
    "author_agent",
    "adapter",
    "model",
    "effort",
    "commit_sha",
    "valid_at",
    "invalid_at",
    "recorded_at",
    "supersedes_id",
    "trust_state",
    "verification_state",
    "review_state",
    "integration_state",
    "access_count",
    "last_accessed_at",
    "content_hash",
    "redaction",
    "poisoning_risk",
    "poisoning_matches",
    "subject_key",
)

# Both are built from _MEMORY_COLUMNS, a module constant. Every value is bound;
# no caller input reaches the SQL text.
_SELECT_MEMORY = f"SELECT {', '.join(_MEMORY_COLUMNS)} FROM memories"  # noqa: S608  # nosec B608
_UPSERT_MEMORY = (
    f"INSERT INTO memories ({', '.join(_MEMORY_COLUMNS)}) "  # noqa: S608  # nosec B608
    f"VALUES ({', '.join('?' for _ in _MEMORY_COLUMNS)}) "
    "ON CONFLICT(memory_id) DO UPDATE SET "
    + ", ".join(f"{c} = excluded.{c}" for c in _MEMORY_COLUMNS if c != "memory_id")
)


class MemoryRepository:
    """Read and write memory projections."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # -- write -------------------------------------------------------------

    def upsert(self, memory: Memory) -> None:
        with self.db.tx() as conn:
            conn.execute(_UPSERT_MEMORY, _memory_to_row(memory))

    def upsert_many(self, memories: list[Memory]) -> None:
        if not memories:
            return
        with self.db.tx() as conn:
            conn.executemany(_UPSERT_MEMORY, [_memory_to_row(m) for m in memories])

    def transition(self, memory: Memory, transition: Transition) -> None:
        """Persist a state change and its transition row atomically.

        The pairing is the point: a promoted memory with no transition row would
        be a trust claim with no audit trail, and an audit trail with no memory
        would be a phantom. One transaction, or neither.
        """
        with self.db.tx() as conn:
            if transition.allowed:
                conn.execute(_UPSERT_MEMORY, _memory_to_row(memory))
            conn.execute(
                "INSERT INTO memory_transitions (transition_id, memory_id, from_state, "
                "to_state, policy_rule, evidence_event_ids, actor, actor_source, "
                "recorded_at, scope, allowed, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    transition.transition_id,
                    transition.memory_id,
                    transition.from_state.value,
                    transition.to_state.value,
                    transition.policy_rule,
                    json.dumps(list(transition.evidence_event_ids), separators=(",", ":")),
                    transition.actor,
                    transition.actor_source.value,
                    transition.recorded_at,
                    transition.scope,
                    1 if transition.allowed else 0,
                    transition.note,
                ),
            )

    def record_refusal(self, transition: Transition) -> None:
        """Persist a refused transition. No memory row changes."""
        self.transition(
            Memory.model_construct(memory_id=transition.memory_id),  # not written
            transition.model_copy(update={"allowed": False}),
        )

    def touch(self, memory_ids: tuple[str, ...], *, when: str | None = None) -> None:
        """Record that memories were retrieved.

        Feeds the usage component of ranking. Weighted weakly (0.15) because
        usage is a popularity signal and self-reinforcing: a record that ranks
        highly gets accessed, which makes it rank higher.
        """
        if not memory_ids:
            return
        stamp = when or _time.now()
        with self.db.tx() as conn:
            conn.executemany(
                "UPDATE memories SET access_count = access_count + 1, "
                "last_accessed_at = ? WHERE memory_id = ?",
                [(stamp, mid) for mid in memory_ids],
            )

    def delete_all_projections(self, *, project_id: str | None = None) -> None:
        """Drop projections ahead of a rebuild.

        Only ever touches derived tables. The journal is append-only and is not
        reachable from here.
        """
        with self.db.tx() as conn:
            if project_id is None:
                conn.execute("DELETE FROM memory_links")
                conn.execute("DELETE FROM contradictions")
                conn.execute("DELETE FROM failure_signatures")
                conn.execute("DELETE FROM memory_vectors")
                conn.execute("DELETE FROM memory_transitions")
                conn.execute("DELETE FROM memories")
            else:
                conn.execute("DELETE FROM memory_links WHERE project_id = ?", (project_id,))
                conn.execute("DELETE FROM contradictions WHERE project_id = ?", (project_id,))
                conn.execute("DELETE FROM failure_signatures WHERE project_id = ?", (project_id,))
                conn.execute(
                    "DELETE FROM memory_vectors WHERE memory_id IN "
                    "(SELECT memory_id FROM memories WHERE project_id = ?)",
                    (project_id,),
                )
                conn.execute(
                    "DELETE FROM memory_transitions WHERE memory_id IN "
                    "(SELECT memory_id FROM memories WHERE project_id = ?)",
                    (project_id,),
                )
                conn.execute("DELETE FROM memories WHERE project_id = ?", (project_id,))

    # -- read --------------------------------------------------------------

    def get(self, memory_id: str) -> Memory | None:
        row = self.db.query_one(f"{_SELECT_MEMORY} WHERE memory_id = ?", (memory_id,))
        return None if row is None else _row_to_memory(row)

    def get_many(self, memory_ids: tuple[str, ...]) -> list[Memory]:
        if not memory_ids:
            return []
        placeholders = ", ".join("?" for _ in memory_ids)
        rows = self.db.query(
            f"{_SELECT_MEMORY} WHERE memory_id IN ({placeholders})",
            tuple(memory_ids),
        )
        return [_row_to_memory(r) for r in rows]

    def find(self, spec: MemoryFilter) -> list[Memory]:
        clauses, params = self._build_filter(spec)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"{_SELECT_MEMORY}{where} ORDER BY recorded_at DESC, memory_id LIMIT ? OFFSET ?"
        params.extend([spec.limit, spec.offset])
        return [_row_to_memory(r) for r in self.db.query(sql, tuple(params))]

    def count(self, spec: MemoryFilter) -> int:
        clauses, params = self._build_filter(spec)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        # `where` is assembled from code-defined clause fragments; the values
        # they reference are bound in `params`.
        sql = f"SELECT COUNT(*) FROM memories{where}"  # noqa: S608  # nosec B608
        return int(self.db.scalar(sql, tuple(params)) or 0)

    def _build_filter(self, spec: MemoryFilter) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []

        if spec.project_id is not None:
            clauses.append("project_id = ?")
            params.append(spec.project_id)
        if spec.memory_types:
            placeholders = ", ".join("?" for _ in spec.memory_types)
            clauses.append(f"memory_type IN ({placeholders})")
            params.extend(t.value for t in spec.memory_types)

        if spec.trust_states:
            placeholders = ", ".join("?" for _ in spec.trust_states)
            clauses.append(f"trust_state IN ({placeholders})")
            params.extend(s.value for s in spec.trust_states)
        else:
            if not spec.include_terminal:
                placeholders = ", ".join("?" for _ in TERMINAL_STATES)
                clauses.append(f"trust_state NOT IN ({placeholders})")
                params.extend(sorted(s.value for s in TERMINAL_STATES))
            if spec.min_trust is not None:
                allowed = [s.value for s in TrustState if rank(s) >= rank(spec.min_trust)]
                if allowed:
                    placeholders = ", ".join("?" for _ in allowed)
                    clauses.append(f"trust_state IN ({placeholders})")
                    params.extend(allowed)

        if spec.current_only:
            clauses.append("invalid_at IS NULL")
        for field in (
            "branch",
            "commit_sha",
            "run_id",
            "task_id",
            "attempt_id",
            "subject_key",
        ):
            value = getattr(spec, field)
            if value is not None:
                clauses.append(f"{field} = ?")
                params.append(value)
        if spec.author_agent is not None:
            clauses.append("author_agent = ?")
            params.append(spec.author_agent)
        return clauses, params

    def iter_all(self, *, project_id: str | None = None, batch: int = 500) -> Iterator[Memory]:
        offset = 0
        while True:
            if project_id is None:
                sql = f"{_SELECT_MEMORY} ORDER BY memory_id LIMIT ? OFFSET ?"
                params: tuple[Any, ...] = (batch, offset)
            else:
                sql = f"{_SELECT_MEMORY} WHERE project_id = ? ORDER BY memory_id LIMIT ? OFFSET ?"
                params = (project_id, batch, offset)
            rows = self.db.query(sql, params)
            if not rows:
                return
            for row in rows:
                yield _row_to_memory(row)
            offset += batch

    def by_content_hash(self, project_id: str, content_hash: str) -> Memory | None:
        """Find an existing record with identical content.

        Deterministic writers derive the same record from the same event every
        time (ADR-0007), so this is how a rebuild recognises what it already has
        instead of duplicating it.
        """
        row = self.db.query_one(
            f"{_SELECT_MEMORY} WHERE project_id = ? AND content_hash = ? "
            "ORDER BY recorded_at LIMIT 1",
            (project_id, content_hash),
        )
        return None if row is None else _row_to_memory(row)

    # -- transitions -------------------------------------------------------

    def transitions_for(self, memory_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT transition_id, memory_id, from_state, to_state, policy_rule, "
            "evidence_event_ids, actor, actor_source, recorded_at, scope, allowed, note "
            "FROM memory_transitions WHERE memory_id = ? "
            "ORDER BY recorded_at DESC, transition_id DESC LIMIT ?",
            (memory_id, limit),
        )
        return [
            {
                "transition_id": r["transition_id"],
                "memory_id": r["memory_id"],
                "from_state": r["from_state"],
                "to_state": r["to_state"],
                "policy_rule": r["policy_rule"],
                "evidence_event_ids": json.loads(r["evidence_event_ids"]),
                "actor": r["actor"],
                "actor_source": r["actor_source"],
                "recorded_at": r["recorded_at"],
                "scope": r["scope"],
                "allowed": bool(r["allowed"]),
                "note": r["note"],
            }
            for r in rows
        ]

    def refusals(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Recent refused transitions. A cluster of these is a security signal."""
        rows = self.db.query(
            "SELECT memory_id, from_state, to_state, policy_rule, actor, actor_source, "
            "recorded_at, note FROM memory_transitions WHERE allowed = 0 "
            "ORDER BY recorded_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    # -- links -------------------------------------------------------------

    def add_link(self, *, project_id: str, from_id: str, to_id: str, link_type: str) -> None:
        with self.db.tx() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO memory_links "
                "(link_id, project_id, from_id, to_id, link_type, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (_ids.new_id(), project_id, from_id, to_id, link_type, _time.now()),
            )

    def links_from(self, memory_id: str, *, link_type: str | None = None) -> list[dict[str, Any]]:
        if link_type is None:
            rows = self.db.query(
                "SELECT from_id, to_id, link_type, recorded_at FROM memory_links WHERE from_id = ?",
                (memory_id,),
            )
        else:
            rows = self.db.query(
                "SELECT from_id, to_id, link_type, recorded_at FROM memory_links "
                "WHERE from_id = ? AND link_type = ?",
                (memory_id, link_type),
            )
        return [dict(r) for r in rows]

    def links_to(self, memory_id: str, *, link_type: str | None = None) -> list[dict[str, Any]]:
        if link_type is None:
            rows = self.db.query(
                "SELECT from_id, to_id, link_type, recorded_at FROM memory_links WHERE to_id = ?",
                (memory_id,),
            )
        else:
            rows = self.db.query(
                "SELECT from_id, to_id, link_type, recorded_at FROM memory_links "
                "WHERE to_id = ? AND link_type = ?",
                (memory_id, link_type),
            )
        return [dict(r) for r in rows]

    # -- contradictions ----------------------------------------------------

    def add_contradiction(
        self, *, project_id: str, subject_key: str, memory_id_a: str, memory_id_b: str
    ) -> None:
        # Order the pair so the same conflict is not recorded twice under two
        # orderings.
        first, second = sorted((memory_id_a, memory_id_b))
        existing = self.db.query_one(
            "SELECT contradiction_id FROM contradictions WHERE memory_id_a = ? AND memory_id_b = ?",
            (first, second),
        )
        if existing is not None:
            return
        with self.db.tx() as conn:
            conn.execute(
                "INSERT INTO contradictions (contradiction_id, project_id, subject_key, "
                "memory_id_a, memory_id_b, detected_at) VALUES (?, ?, ?, ?, ?, ?)",
                (_ids.new_id(), project_id, subject_key, first, second, _time.now()),
            )

    def contradicted_ids(self, project_id: str) -> set[str]:
        """Memory IDs involved in an unresolved contradiction."""
        rows = self.db.query(
            "SELECT memory_id_a, memory_id_b FROM contradictions "
            "WHERE project_id = ? AND resolved = 0",
            (project_id,),
        )
        out: set[str] = set()
        for row in rows:
            out.add(str(row["memory_id_a"]))
            out.add(str(row["memory_id_b"]))
        return out

    def contradictions(
        self, project_id: str, *, unresolved_only: bool = True
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT contradiction_id, subject_key, memory_id_a, memory_id_b, "
            "detected_at, resolved, resolution_note FROM contradictions WHERE project_id = ?"
        )
        params: tuple[Any, ...] = (project_id,)
        if unresolved_only:
            sql += " AND resolved = 0"
        return [dict(r) for r in self.db.query(sql + " ORDER BY detected_at DESC", params)]

    # -- failure signatures ------------------------------------------------

    def record_signature(
        self,
        *,
        signature: str,
        project_id: str,
        memory_id: str,
        command: str,
        error_kind: str,
        when: str,
    ) -> int:
        """Record a failure signature occurrence; return the new occurrence count.

        Repetition is what turns "this once failed" into "this keeps failing",
        which is the difference between a note and a warning worth interrupting
        for.
        """
        with self.db.tx() as conn:
            existing = conn.execute(
                "SELECT occurrences FROM failure_signatures "
                "WHERE signature = ? AND project_id = ? AND memory_id = ?",
                (signature, project_id, memory_id),
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO failure_signatures (signature, project_id, memory_id, "
                    "command, error_kind, occurrences, first_seen_at, last_seen_at) "
                    "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                    (signature, project_id, memory_id, command, error_kind, when, when),
                )
                return 1
            count = int(existing["occurrences"]) + 1
            conn.execute(
                "UPDATE failure_signatures SET occurrences = ?, last_seen_at = ? "
                "WHERE signature = ? AND project_id = ? AND memory_id = ?",
                (count, when, signature, project_id, memory_id),
            )
            return count

    def signature_rows(self, project_id: str, signature: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT signature, memory_id, command, error_kind, occurrences, "
            "first_seen_at, last_seen_at, resolved_by_id FROM failure_signatures "
            "WHERE project_id = ? AND signature = ? ORDER BY occurrences DESC",
            (project_id, signature),
        )
        return [dict(r) for r in rows]

    def all_signatures(self, project_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT signature, memory_id, command, error_kind, occurrences, "
            "first_seen_at, last_seen_at, resolved_by_id FROM failure_signatures "
            "WHERE project_id = ? ORDER BY occurrences DESC, last_seen_at DESC",
            (project_id,),
        )
        return [dict(r) for r in rows]

    def set_signature_resolution(
        self, *, project_id: str, signature: str, resolved_by_id: str
    ) -> None:
        with self.db.tx() as conn:
            conn.execute(
                "UPDATE failure_signatures SET resolved_by_id = ? "
                "WHERE project_id = ? AND signature = ?",
                (resolved_by_id, project_id, signature),
            )

    # -- projection state --------------------------------------------------

    def projection_seq(self) -> int:
        return int(self.db.scalar("SELECT last_event_seq FROM projection_state WHERE id = 1") or 0)

    def set_projection_seq(self, seq: int, *, rebuilt: bool = False) -> None:
        with self.db.tx() as conn:
            if rebuilt:
                conn.execute(
                    "UPDATE projection_state SET last_event_seq = ?, rebuilt_at = ? WHERE id = 1",
                    (seq, _time.now()),
                )
            else:
                conn.execute("UPDATE projection_state SET last_event_seq = ? WHERE id = 1", (seq,))


def _memory_to_row(memory: Memory) -> tuple[Any, ...]:
    return (
        memory.memory_id,
        memory.schema_version,
        memory.memory_type.value,
        json.dumps(memory.content, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        memory.text,
        memory.scope.level.value,
        memory.scope.project_id,
        memory.scope.repository_id,
        memory.scope.branch,
        memory.run_id,
        memory.task_id,
        memory.attempt_id,
        json.dumps(list(memory.source_event_ids), separators=(",", ":")),
        memory.source.value,
        memory.author_agent,
        memory.adapter,
        memory.model,
        memory.effort,
        memory.commit_sha,
        memory.valid_at,
        memory.invalid_at,
        memory.recorded_at,
        memory.supersedes_id,
        memory.trust_state.value,
        memory.verification_state.value,
        memory.review_state.value,
        memory.integration_state.value,
        memory.access_count,
        memory.last_accessed_at,
        memory.content_hash,
        json.dumps(memory.redaction, sort_keys=True, separators=(",", ":")),
        memory.poisoning_risk,
        json.dumps(list(memory.poisoning_matches), separators=(",", ":")),
        memory.subject_key,
    )


def _row_to_memory(row: sqlite3.Row) -> Memory:
    return Memory(
        memory_id=row["memory_id"],
        schema_version=int(row["schema_version"]),
        memory_type=MemoryType(row["memory_type"]),
        content=json.loads(row["content"]),
        text=row["text"],
        scope=Scope(
            level=ScopeLevel(row["scope_level"]),
            project_id=row["project_id"],
            repository_id=row["repository_id"],
            branch=row["branch"],
            run_id=row["run_id"],
            task_id=row["task_id"],
            attempt_id=row["attempt_id"],
            agent_profile=row["author_agent"],
        ),
        run_id=row["run_id"],
        task_id=row["task_id"],
        attempt_id=row["attempt_id"],
        source_event_ids=tuple(json.loads(row["source_event_ids"])),
        source=Source(row["source"]),
        author_agent=row["author_agent"],
        adapter=row["adapter"],
        model=row["model"],
        effort=row["effort"],
        commit_sha=row["commit_sha"],
        valid_at=row["valid_at"],
        invalid_at=row["invalid_at"],
        recorded_at=row["recorded_at"],
        supersedes_id=row["supersedes_id"],
        trust_state=TrustState(row["trust_state"]),
        verification_state=VerificationState(row["verification_state"]),
        review_state=ReviewState(row["review_state"]),
        integration_state=IntegrationState(row["integration_state"]),
        access_count=int(row["access_count"]),
        last_accessed_at=row["last_accessed_at"],
        content_hash=row["content_hash"],
        redaction=json.loads(row["redaction"]),
        poisoning_risk=float(row["poisoning_risk"]),
        poisoning_matches=tuple(json.loads(row["poisoning_matches"])),
        subject_key=row["subject_key"],
    )
