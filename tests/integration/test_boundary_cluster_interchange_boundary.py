"""What `import_records` stores, and what it only claims to store.

Two defects with the same shape — the import path reporting success it had not
achieved:

* `event_id` is a global key, but the duplicate map was built from one project.
  An id already held by *another* project therefore looked new, and the append
  raised `IntegrityError` out of the batch — rolling back every valid record in
  the file, with no `ImportIssue` to show for it. ADR-0011 says a duplicate id
  with different content is a reported conflict and the import continues.
* Memory and transition records were counted as `accepted` and never stored.
  A memories-only file reported "accepted: 1 … ok: True" and wrote nothing.
"""

from __future__ import annotations

from pathlib import Path

from provalume.interchange import jsonl
from provalume.interchange.hashing import canonical_json, hash_payload
from provalume.schemas.events import EventType
from provalume.sdk.client import Provalume
from provalume.store.db import open_database


def event_record(event_id: str, *, project_id: str, statement: str) -> dict:
    payload = {"statement": statement, "subject": "boundary"}
    return {
        "rv": 1,
        "kind": "event",
        "id": event_id,
        "schema_version": 1,
        "event_type": EventType.FACT_OBSERVED.value,
        "recorded_at": "2026-01-01T00:00:00.000Z",
        "project_id": project_id,
        "source": "kernel",
        "payload": payload,
        "payload_hash": hash_payload(payload),
    }


def write_events(directory: Path, records: list[dict]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / jsonl.EVENTS_FILE).write_text(
        "".join(canonical_json(r) + "\n" for r in records), encoding="utf-8"
    )
    return directory


# --- A cross-project id collision -------------------------------------------


def test_an_id_held_by_another_project_is_a_conflict_not_a_crash(tmp_path: Path) -> None:
    db = open_database(tmp_path / "shared.db")
    other = Provalume(db, project_id="other", git=None)
    collided = other.record_event(
        EventType.FACT_OBSERVED, payload={"statement": "held elsewhere", "subject": "boundary"}
    )
    demo = Provalume(db, project_id="demo", git=None)

    directory = write_events(
        tmp_path / "export",
        [
            event_record(collided.event_id, project_id="demo", statement="different content"),
            event_record(
                "01KYDBHEEZQCK79D36DMXFJEZZ", project_id="demo", statement="perfectly fine"
            ),
        ],
    )
    result = demo.import_records(directory)

    assert result.conflicts, "the collision was not reported"
    assert not result.ok
    assert result.accepted == 1, "the valid record was rolled back with the bad one"
    statements = [e.payload["statement"] for e in demo.events()]
    assert "perfectly fine" in statements


def test_an_id_held_by_another_project_with_the_same_content_is_a_duplicate(
    tmp_path: Path,
) -> None:
    """Same id, same payload: the journal already holds it, so it is not new."""
    db = open_database(tmp_path / "shared.db")
    other = Provalume(db, project_id="other", git=None)
    shared = other.record_event(
        EventType.FACT_OBSERVED, payload={"statement": "shared", "subject": "boundary"}
    )
    demo = Provalume(db, project_id="demo", git=None)

    directory = write_events(
        tmp_path / "export",
        [event_record(shared.event_id, project_id="demo", statement="shared")],
    )
    result = demo.import_records(directory)

    assert result.skipped_duplicates == 1
    assert result.ok


def test_a_conflict_found_only_at_append_time_is_reported_not_raised(tmp_path: Path) -> None:
    """The duplicate map is a fast path; the journal is the authority.

    Between the scan and the append another writer can land the same id — so the
    batch can still fail, and when it does the file must not be lost with it.
    """
    demo = Provalume(open_database(tmp_path / "target.db"), project_id="demo", git=None)
    directory = write_events(
        tmp_path / "export",
        [
            event_record("01KYDBHEEZQCK79D36DMXFJEY1", project_id="demo", statement="first"),
            event_record("01KYDBHEEZQCK79D36DMXFJEY2", project_id="demo", statement="second"),
        ],
    )
    result = demo.import_records(directory, apply=False)
    assert len(result.events) == 2
    raced = result.events[0].model_copy(
        update={"payload": {"statement": "landed first", "subject": "boundary"}}
    )
    demo.journal.append(raced)

    demo._append_imported(result)

    assert [str(c) for c in result.conflicts], "the collision was swallowed"
    assert result.accepted == 1
    assert "second" in [e.payload["statement"] for e in demo.events()]


# --- Memory and transition records are read, not stored ---------------------


def test_a_memories_only_import_does_not_report_records_it_never_stores(
    tmp_path: Path,
) -> None:
    source = Provalume(open_database(":memory:"), project_id="demo", git=None)
    source.record_fact(statement="the build uses uv", subject="build")
    out = tmp_path / "export"
    source.export(out)
    memories_only = tmp_path / "memories-only"
    memories_only.mkdir()
    (memories_only / jsonl.MEMORIES_FILE).write_text(
        (out / jsonl.MEMORIES_FILE).read_text(encoding="utf-8"), encoding="utf-8"
    )

    target = Provalume(open_database(":memory:"), project_id="demo", git=None)
    result = target.import_records(memories_only)

    assert result.memories, "the file did contain memory records"
    assert result.accepted == 0, "reported records it stored nowhere"
    assert not target.memory_records(limit=10)
    summary = jsonl.summarize(result)
    assert "not stored" in summary


def test_a_full_round_trip_still_reconstructs_every_memory(tmp_path: Path) -> None:
    """The counting fix must not have narrowed what an import actually restores."""
    source = Provalume(open_database(":memory:"), project_id="demo", git=None)
    source.record_verification(command="pytest -q", passed=True, purpose="unit tests")
    source.record_fact(statement="the build uses uv", subject="build")
    out = tmp_path / "export"
    source.export(out)

    target = Provalume(open_database(":memory:"), project_id="demo", git=None)
    result = target.import_records(out)

    assert result.accepted == len(source.events())
    assert result.ok
    target.rebuild()
    assert len(target.memory_records(limit=50)) == len(source.memory_records(limit=50))
