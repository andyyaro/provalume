"""JSONL export and import (ADR-0011).

SQLite is the operational store; JSONL is what crosses machines and enters Git.

Determinism is the design constraint: sorted keys, fixed separators, records
ordered by ``(kind, id)``. Exporting the same database twice produces
byte-identical files, so a diff shows what changed rather than how the dictionary
happened to iterate.

The import rules exist because **an imported record is untrusted input** (threat
T17). Its claimed trust state carries no weight — trust is re-derived locally from
evidence that also imported and also validated. A record from the future is
rejected rather than partially interpreted, and a duplicate ID with different
content is a conflict rather than an overwrite, because overwriting is how forged
provenance would get in through the one path that is supposed to be append-only.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from provalume import _time
from provalume.errors import (
    InterchangeError,
    OversizedInputError,
    UnknownRecordVersion,
    ValidationError,
)
from provalume.interchange.hashing import canonical_json, hash_payload
from provalume.interchange.signatures import Verifier
from provalume.policy.admission import admit_event
from provalume.policy.scope import confine
from provalume.schemas.events import Event, EventType
from provalume.schemas.memories import Memory, MemoryType, Transition
from provalume.schemas.scope import Scope, ScopeLevel
from provalume.schemas.trust import (
    IntegrationState,
    ReviewState,
    Source,
    TrustState,
    VerificationState,
)

#: Interchange record version. Bumping this is a compatibility event (ADR-0017).
RECORD_VERSION: Final = 1

#: Bounds, so a hostile file cannot exhaust memory (threat T24/T25).
MAX_LINE_BYTES: Final = 1024 * 1024
MAX_RECORDS_PER_FILE: Final = 1_000_000

KIND_EVENT: Final = "event"
KIND_MEMORY: Final = "memory"
KIND_TRANSITION: Final = "transition"

EVENTS_FILE: Final = "events.jsonl"
MEMORIES_FILE: Final = "memories.jsonl"
TRANSITIONS_FILE: Final = "transitions.jsonl"

#: Fields deliberately NOT exported. `seq`, `event_hash`, and `prev_event_hash`
#: describe this database's local chain, not the record. Exporting them would
#: invite an importer to treat a foreign chain as its own, and the chain is a
#: local tamper-evidence mechanism rather than a global ledger (ADR-0002).
_LOCAL_ONLY_FIELDS: Final[frozenset[str]] = frozenset(
    {"seq", "event_hash", "prev_event_hash"}
)


# --- Export ----------------------------------------------------------------


def event_to_record(event: Event) -> dict[str, Any]:
    """Serialise an event for interchange."""
    record: dict[str, Any] = {
        "rv": RECORD_VERSION,
        "kind": KIND_EVENT,
        "id": event.event_id,
        "schema_version": event.schema_version,
        "event_type": event.event_type.value,
        "recorded_at": event.recorded_at,
        "project_id": event.project_id,
        "source": event.source.value,
        "payload": event.payload,
        "payload_hash": event.payload_hash or hash_payload(event.payload),
    }
    optional = {
        "occurred_at": event.occurred_at,
        "repository_id": event.repository_id,
        "run_id": event.run_id,
        "task_id": event.task_id,
        "attempt_id": event.attempt_id,
        "agent_profile": event.agent_profile,
        "adapter": event.adapter,
        "model": event.model,
        "effort": event.effort,
        "branch": event.branch,
        "worktree": event.worktree,
        "base_commit": event.base_commit,
        "commit_sha": event.commit_sha,
        "causal_parent_event_id": event.causal_parent_event_id,
    }
    # Omit absent optionals rather than emitting nulls: a file full of nulls is
    # noisier to diff, and omission is unambiguous here because the schema
    # defines every optional as nullable.
    record.update({k: v for k, v in optional.items() if v is not None})
    if event.redaction:
        record["redaction"] = event.redaction
    if event.integrity:
        record["integrity"] = event.integrity

    # Enforced rather than merely intended: a future field added to the
    # serialiser above cannot leak this database's local chain into a file that
    # crosses machines.
    for local_only in _LOCAL_ONLY_FIELDS:
        record.pop(local_only, None)
    return record


def memory_to_record(memory: Memory) -> dict[str, Any]:
    record: dict[str, Any] = {
        "rv": RECORD_VERSION,
        "kind": KIND_MEMORY,
        "id": memory.memory_id,
        "schema_version": memory.schema_version,
        "memory_type": memory.memory_type.value,
        "content": memory.content,
        "text": memory.text,
        "scope_level": memory.scope.level.value,
        "project_id": memory.scope.project_id,
        "source": memory.source.value,
        "source_event_ids": list(memory.source_event_ids),
        "valid_at": memory.valid_at,
        "recorded_at": memory.recorded_at,
        "trust_state": memory.trust_state.value,
        "verification_state": memory.verification_state.value,
        "review_state": memory.review_state.value,
        "integration_state": memory.integration_state.value,
        "content_hash": memory.content_hash,
        "poisoning_risk": memory.poisoning_risk,
        "subject_key": memory.subject_key,
    }
    optional = {
        "repository_id": memory.scope.repository_id,
        "branch": memory.scope.branch,
        "run_id": memory.run_id,
        "task_id": memory.task_id,
        "attempt_id": memory.attempt_id,
        "author_agent": memory.author_agent,
        "adapter": memory.adapter,
        "model": memory.model,
        "effort": memory.effort,
        "commit_sha": memory.commit_sha,
        "invalid_at": memory.invalid_at,
        "supersedes_id": memory.supersedes_id,
    }
    record.update({k: v for k, v in optional.items() if v is not None})
    if memory.poisoning_matches:
        record["poisoning_matches"] = list(memory.poisoning_matches)
    if memory.redaction:
        record["redaction"] = memory.redaction
    return record


def transition_to_record(transition: dict[str, Any]) -> dict[str, Any]:
    return {
        "rv": RECORD_VERSION,
        "kind": KIND_TRANSITION,
        "id": transition["transition_id"],
        "memory_id": transition["memory_id"],
        "from_state": transition["from_state"],
        "to_state": transition["to_state"],
        "policy_rule": transition["policy_rule"],
        "evidence_event_ids": list(transition.get("evidence_event_ids", [])),
        "actor": transition["actor"],
        "actor_source": transition["actor_source"],
        "recorded_at": transition["recorded_at"],
        "scope": transition.get("scope", ""),
        "allowed": bool(transition.get("allowed", True)),
        "note": transition.get("note", ""),
    }


def write_records(path: Path, records: list[dict[str, Any]]) -> int:
    """Write records deterministically, sorted by ``(kind, id)``.

    Sorting by ID rather than by insertion order is what makes two machines
    produce comparable files. Because IDs are time-sortable ULIDs, new records
    land near the end, so Git diffs stay append-mostly instead of churning.
    """
    ordered = sorted(records, key=lambda r: (str(r.get("kind", "")), str(r.get("id", ""))))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in ordered:
            handle.write(canonical_json(record))
            handle.write("\n")
    return len(ordered)


@dataclass
class ExportResult:
    directory: Path
    events: int = 0
    memories: int = 0
    transitions: int = 0
    files: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.events + self.memories + self.transitions


def export(
    *,
    directory: Path | str,
    events: list[Event],
    memories: list[Memory],
    transitions: list[dict[str, Any]],
    signer: Any = None,
) -> ExportResult:
    """Write a full export.

    ``signer`` is a callable taking a record and returning a signature block. Kept
    as a callable rather than a key so the caller decides scheme and key handling,
    and no key material passes through this module.
    """
    out_dir = Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)

    event_records = [event_to_record(e) for e in events]
    memory_records = [memory_to_record(m) for m in memories]
    transition_records = [transition_to_record(t) for t in transitions]

    if signer is not None:
        for record in (*event_records, *memory_records, *transition_records):
            record["signature"] = signer(record)

    result = ExportResult(directory=out_dir)
    result.events = write_records(out_dir / EVENTS_FILE, event_records)
    result.memories = write_records(out_dir / MEMORIES_FILE, memory_records)
    result.transitions = write_records(out_dir / TRANSITIONS_FILE, transition_records)
    result.files = [EVENTS_FILE, MEMORIES_FILE, TRANSITIONS_FILE]
    return result


# --- Import ----------------------------------------------------------------


@dataclass
class ImportIssue:
    """One rejected or quarantined record.

    Line numbers are kept so a person can find the offending record in a file
    that may hold a million lines.
    """

    line: int
    file: str
    reason: str
    record_id: str = ""

    def __str__(self) -> str:
        where = f"{self.file}:{self.line}"
        which = f" [{self.record_id}]" if self.record_id else ""
        return f"{where}{which}: {self.reason}"


@dataclass
class ImportResult:
    events: list[Event] = field(default_factory=list)
    memories: list[Memory] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)
    skipped_duplicates: int = 0
    conflicts: list[ImportIssue] = field(default_factory=list)
    rejected: list[ImportIssue] = field(default_factory=list)
    quarantined: list[ImportIssue] = field(default_factory=list)

    @property
    def accepted(self) -> int:
        """Records that will actually be stored, which is events and only events.

        Memory and transition records are parsed, validated, and checked for
        divergent supersession — but a memory is a projection of events (ADR-0002)
        and the target rebuilds its own from the events it just accepted, so
        nothing here persists them. Counting them as accepted asserted a
        durability that does not exist: a memories-only file reported "accepted:
        1 … ok: True" and wrote nothing.
        """
        return len(self.events)

    @property
    def ok(self) -> bool:
        """Whether the import completed without conflicts or rejections.

        Duplicates do not count: skipping an already-present record is the
        expected outcome of re-importing an overlapping export, not a problem.
        """
        return not self.conflicts and not self.rejected


def iter_lines(path: Path) -> Iterator[tuple[int, str]]:
    """Yield ``(line_number, text)``, enforcing the per-line size cap.

    An oversized line is yielded as empty so the caller records the issue and
    continues. One hostile line must not abort an otherwise valid file.
    """
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for number, raw in enumerate(handle, start=1):
            if len(raw.encode("utf-8")) > MAX_LINE_BYTES:
                yield number, ""
                continue
            stripped = raw.strip()
            if stripped:
                yield number, stripped


def _parse_record(text: str) -> dict[str, Any]:
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        msg = "record is not a JSON object"
        raise InterchangeError(msg)
    return parsed


def _check_version(record: dict[str, Any]) -> None:
    raw = record.get("rv")
    if not isinstance(raw, int):
        msg = "record has no integer 'rv' version field"
        raise InterchangeError(msg)
    if raw > RECORD_VERSION:
        msg = (
            f"record version {raw} is newer than this build supports "
            f"({RECORD_VERSION}). Refusing to interpret it partially: a record "
            "that cannot be fully validated cannot be safely trusted."
        )
        raise UnknownRecordVersion(msg)
    if raw < 1:
        msg = f"record version {raw} is not valid"
        raise InterchangeError(msg)


def record_to_event(record: dict[str, Any]) -> Event:
    """Rebuild an event from an interchange record.

    Hash fields are left empty: the receiving journal recomputes ``payload_hash``
    and assigns a fresh position in its own chain. The exported ``payload_hash``
    is used only to detect conflicts, never adopted verbatim.

    The returned event has **not** been admitted. It is a parse of untrusted
    input, and :func:`import_directory` puts it through
    :func:`provalume.policy.admission.admit_event` before it can reach a journal.
    """
    return Event(
        event_id=str(record["id"]),
        schema_version=int(record.get("schema_version", 1)),
        event_type=EventType(record["event_type"]),
        recorded_at=str(record["recorded_at"]),
        occurred_at=record.get("occurred_at"),
        project_id=str(record["project_id"]),
        repository_id=record.get("repository_id"),
        run_id=record.get("run_id"),
        task_id=record.get("task_id"),
        attempt_id=record.get("attempt_id"),
        agent_profile=record.get("agent_profile"),
        adapter=record.get("adapter"),
        model=record.get("model"),
        effort=record.get("effort"),
        branch=record.get("branch"),
        worktree=record.get("worktree"),
        base_commit=record.get("base_commit"),
        commit_sha=record.get("commit_sha"),
        causal_parent_event_id=record.get("causal_parent_event_id"),
        # Always `import`, never the exporter's claimed source. A file cannot
        # promote itself to `kernel` by saying so (threat T17).
        source=Source.IMPORT,
        payload=dict(record.get("payload", {})),
        # The file's `redaction` and `integrity` blocks are dropped rather than
        # copied. Both are claims *about* Provalume's own processing — "this was
        # redacted", "this scored 0.0 for poisoning" — and adopting them verbatim
        # would let a hand-written file assert that a scan it never ran came back
        # clean. `_landing_state` and the promotion gate read
        # `integrity.poisoning.risk`, so a forged 0.0 there disables both.
        # Admission recomputes each from the payload actually being stored.
    )


def record_to_memory(record: dict[str, Any]) -> Memory:
    """Rebuild a memory from an interchange record.

    The record's *claimed* trust state is deliberately discarded. Imported
    memories land at ``quarantined`` and are re-derived locally from evidence
    that also imported and also validated (ADR-0005). Evidence states are kept
    because they are factual claims about what happened, which the local
    promotion rules will then check against actual events.
    """
    return Memory(
        memory_id=str(record["id"]),
        schema_version=int(record.get("schema_version", 1)),
        memory_type=MemoryType(record["memory_type"]),
        content=dict(record.get("content", {})),
        text=str(record.get("text", "")),
        scope=Scope(
            level=ScopeLevel(record.get("scope_level", "branch")),
            project_id=str(record["project_id"]),
            repository_id=record.get("repository_id"),
            branch=record.get("branch"),
            run_id=record.get("run_id"),
            task_id=record.get("task_id"),
            attempt_id=record.get("attempt_id"),
            agent_profile=record.get("author_agent"),
        ),
        run_id=record.get("run_id"),
        task_id=record.get("task_id"),
        attempt_id=record.get("attempt_id"),
        source_event_ids=tuple(record.get("source_event_ids", [])),
        source=Source.IMPORT,
        author_agent=record.get("author_agent"),
        adapter=record.get("adapter"),
        model=record.get("model"),
        effort=record.get("effort"),
        commit_sha=record.get("commit_sha"),
        valid_at=str(record["valid_at"]),
        invalid_at=record.get("invalid_at"),
        recorded_at=str(record["recorded_at"]),
        supersedes_id=record.get("supersedes_id"),
        trust_state=TrustState.QUARANTINED,
        verification_state=VerificationState(record.get("verification_state", "unknown")),
        review_state=ReviewState(record.get("review_state", "none")),
        integration_state=IntegrationState(record.get("integration_state", "none")),
        content_hash=str(record.get("content_hash", "")),
        redaction=dict(record.get("redaction", {})),
        poisoning_risk=float(record.get("poisoning_risk", 0.0)),
        poisoning_matches=tuple(record.get("poisoning_matches", [])),
        subject_key=str(record.get("subject_key", "")),
    )


def record_to_transition(record: dict[str, Any]) -> Transition:
    return Transition(
        transition_id=str(record["id"]),
        memory_id=str(record["memory_id"]),
        from_state=TrustState(record["from_state"]),
        to_state=TrustState(record["to_state"]),
        policy_rule=str(record["policy_rule"]),
        evidence_event_ids=tuple(record.get("evidence_event_ids", [])),
        actor=str(record.get("actor", "")),
        actor_source=Source(record.get("actor_source", "import")),
        recorded_at=str(record["recorded_at"]),
        scope=str(record.get("scope", "")),
        allowed=bool(record.get("allowed", True)),
        note=str(record.get("note", "")),
    )


def import_directory(
    directory: Path | str,
    *,
    project_id: str,
    existing_event_hashes: dict[str, str] | None = None,
    allow_foreign_project: bool = False,
    quarantine_unknown: bool = False,
    verifier: Verifier | None = None,
    root: Path | str | None = None,
    poisoning_threshold: float = 0.5,
) -> ImportResult:
    """Import a JSONL export directory.

    ``existing_event_hashes`` maps ``event_id -> payload_hash`` for what the
    target already holds, so a duplicate can be told apart from a conflict
    without a query per line.

    Every accepted event has been through :func:`admit_event`, so
    ``result.events`` is safe to hand to a journal: redacted, poisoning-scanned,
    within the size caps. Admission happens here rather than in the caller
    because this is the only place that still knows which line of which file a
    record came from, and a record that fails admission has to be reportable as
    an :class:`ImportIssue` rather than an exception that aborts the file.

    ``result.memories`` and ``result.transitions`` are parsed for inspection —
    :func:`_detect_supersession_conflicts` reads them — not for storage. A memory
    is a projection of events, and an importer rebuilds its own from the events it
    accepted, which is why they do not count towards ``accepted``.

    ``existing_event_hashes`` should cover the *whole* target journal rather than
    one project: ``event_id`` is a global key, so a map scoped to one project
    reports "new" for an id another project already holds.
    """
    base = Path(directory)
    if root is not None:
        base = confine(base, root)
    if not base.is_dir():
        msg = f"not a directory: {base}"
        raise InterchangeError(msg)

    result = ImportResult()
    known = existing_event_hashes or {}

    for filename, kind in (
        (EVENTS_FILE, KIND_EVENT),
        (MEMORIES_FILE, KIND_MEMORY),
        (TRANSITIONS_FILE, KIND_TRANSITION),
    ):
        path = base / filename
        if not path.exists():
            continue

        count = 0
        for line_no, text in iter_lines(path):
            count += 1
            if count > MAX_RECORDS_PER_FILE:
                result.rejected.append(
                    ImportIssue(line_no, filename, "file exceeds the record cap")
                )
                break
            if not text:
                result.rejected.append(
                    ImportIssue(line_no, filename, "line exceeds the size cap")
                )
                continue

            try:
                record = _parse_record(text)
            except (json.JSONDecodeError, InterchangeError) as exc:
                result.rejected.append(ImportIssue(line_no, filename, f"malformed: {exc}"))
                continue

            record_id = str(record.get("id", ""))

            try:
                _check_version(record)
            except UnknownRecordVersion as exc:
                issue = ImportIssue(line_no, filename, str(exc), record_id)
                if quarantine_unknown:
                    result.quarantined.append(issue)
                else:
                    result.rejected.append(issue)
                continue
            except InterchangeError as exc:
                result.rejected.append(ImportIssue(line_no, filename, str(exc), record_id))
                continue

            if record.get("kind") != kind:
                result.rejected.append(
                    ImportIssue(
                        line_no,
                        filename,
                        f"record kind {record.get('kind')!r} does not match {kind!r}",
                        record_id,
                    )
                )
                continue

            if verifier is not None:
                ok, reason = verifier.verify(record)
                if not ok:
                    # Fail closed: quarantine, never accept unverified (T18).
                    result.quarantined.append(
                        ImportIssue(line_no, filename, f"signature: {reason}", record_id)
                    )
                    continue

            record_project = str(record.get("project_id", ""))
            if (
                kind in {KIND_EVENT, KIND_MEMORY}
                and record_project != project_id
                and not allow_foreign_project
            ):
                result.rejected.append(
                    ImportIssue(
                        line_no,
                        filename,
                        f"belongs to project {record_project!r}, not {project_id!r}. "
                        "Pass --allow-foreign-project to import it anyway.",
                        record_id,
                    )
                )
                continue

            try:
                if kind == KIND_EVENT:
                    # Recompute rather than trust. A record's declared
                    # payload_hash is attacker-controlled input like everything
                    # else in the file: taking it at face value would let someone
                    # alter a payload, leave the hash untouched, and have the
                    # change pass as a harmless duplicate.
                    claimed_hash = str(record.get("payload_hash", ""))
                    actual_hash = hash_payload(dict(record.get("payload", {})))

                    if claimed_hash and claimed_hash != actual_hash:
                        result.rejected.append(
                            ImportIssue(
                                line_no,
                                filename,
                                "declared payload_hash does not match the payload "
                                f"(declared {claimed_hash[:19]}…, actual "
                                f"{actual_hash[:19]}…) — the record was altered in "
                                "transit or is forged",
                                record_id,
                            )
                        )
                        continue

                    prior = known.get(record_id)
                    if prior is not None:
                        if prior == actual_hash:
                            result.skipped_duplicates += 1
                        else:
                            result.conflicts.append(
                                ImportIssue(
                                    line_no,
                                    filename,
                                    "an event with this id already exists with different "
                                    "content; refusing to overwrite an append-only record",
                                    record_id,
                                )
                            )
                        continue
                    # Admission, on the same terms as a locally recorded
                    # event: validate, cap, redact, scan — then hash. Skipping
                    # it here would put an unredacted credential on disk and
                    # have the chain attest to it (threat T11), and would let
                    # the file's own poisoning score stand in for one Provalume
                    # never computed.
                    try:
                        admitted = admit_event(
                            record_to_event(record),
                            poisoning_threshold=poisoning_threshold,
                        )
                    except (OversizedInputError, ValidationError) as exc:
                        result.rejected.append(
                            ImportIssue(
                                line_no, filename, f"failed admission: {exc}", record_id
                            )
                        )
                        continue
                    result.events.append(admitted.event)
                elif kind == KIND_MEMORY:
                    result.memories.append(record_to_memory(record))
                else:
                    result.transitions.append(record_to_transition(record))
            except (KeyError, ValueError, TypeError) as exc:
                result.rejected.append(
                    ImportIssue(line_no, filename, f"invalid record: {exc}", record_id)
                )

    _detect_supersession_conflicts(result)
    return result


def _detect_supersession_conflicts(result: ImportResult) -> None:
    """Flag two records claiming to supersede the same predecessor.

    Surfaced, never resolved. Auto-resolving by recency would be auto-deciding
    which of two contributors was right, and the newer record may be the poisoned
    one (ADR-0009).
    """
    claims: dict[str, list[str]] = {}
    for memory in result.memories:
        if memory.supersedes_id:
            claims.setdefault(memory.supersedes_id, []).append(memory.memory_id)

    for predecessor, successors in sorted(claims.items()):
        if len(successors) > 1:
            result.conflicts.append(
                ImportIssue(
                    0,
                    MEMORIES_FILE,
                    f"divergent supersession: {len(successors)} records claim to "
                    f"supersede {predecessor} ({', '.join(sorted(successors))}). "
                    "Both imported; neither auto-resolved.",
                    predecessor,
                )
            )


def summarize(result: ImportResult) -> str:
    """Human-readable import summary."""
    lines = [
        f"accepted:   {result.accepted} events (stored)",
        f"duplicates: {result.skipped_duplicates} (already present, skipped)",
    ]
    if result.memories or result.transitions:
        lines.append(
            f"read only:  {len(result.memories)} memories, "
            f"{len(result.transitions)} transitions "
            "(projections are rebuilt from events; not stored)"
        )
    if result.quarantined:
        lines.append(f"quarantined: {len(result.quarantined)}")
        lines.extend(f"  - {issue}" for issue in result.quarantined[:10])
    if result.conflicts:
        lines.append(f"conflicts:  {len(result.conflicts)}")
        lines.extend(f"  - {issue}" for issue in result.conflicts[:10])
    if result.rejected:
        lines.append(f"rejected:   {len(result.rejected)}")
        lines.extend(f"  - {issue}" for issue in result.rejected[:10])
    return "\n".join(lines)


def export_stamp() -> str:
    """Timestamp for an export manifest, if a caller wants one."""
    return _time.now()
