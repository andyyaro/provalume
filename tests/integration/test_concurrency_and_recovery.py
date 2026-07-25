"""WAL concurrency, crash recovery, corruption detection, and migrations.

These use real on-disk databases: WAL, fsync behaviour, and file corruption have
nothing to test in an in-memory database.
"""

from __future__ import annotations

import contextlib
import multiprocessing
import sqlite3
import subprocess  # nosec B404 - runs this interpreter to simulate a crash
import sys
from pathlib import Path

import pytest

from provalume.errors import IntegrityError, SchemaVersionError, StoreError
from provalume.schemas.events import Event, EventType
from provalume.schemas.trust import Source
from provalume.store.db import Database, open_database
from provalume.store.journal import Journal
from provalume.store.migrations import SCHEMA_VERSION


def make_event(index: int = 0, project: str = "p1") -> Event:
    return Event.create(
        event_type=EventType.VERIFICATION_FAILED,
        project_id=project,
        source=Source.KERNEL,
        payload={"command": f"cmd-{index}", "exit_code": 1},
    )


# --- Pragmas and WAL -------------------------------------------------------


def test_wal_is_enabled_on_disk(file_db: Database) -> None:
    assert file_db.pragma_report()["journal_mode"].lower() == "wal"
    assert not file_db.check_pragmas()


def test_foreign_keys_are_enforced(file_db: Database) -> None:
    assert int(file_db.pragma_report()["foreign_keys"]) == 1


def test_busy_timeout_is_set(file_db: Database) -> None:
    assert int(file_db.pragma_report()["busy_timeout"]) >= 5000


def test_readers_do_not_block_the_writer(tmp_path: Path) -> None:
    """The reason WAL is required: an orchestrator writes while agents read."""
    path = tmp_path / "wal.db"
    writer = open_database(path)
    journal = Journal(writer)
    journal.append(make_event(0))

    reader = open_database(path)
    read_journal = Journal(reader)
    assert read_journal.count() == 1

    # Write again while the reader is open.
    journal.append(make_event(1))
    assert read_journal.count() == 2

    reader.close()
    writer.close()


def test_two_connections_can_write_serially(tmp_path: Path) -> None:
    path = tmp_path / "serial.db"
    first = open_database(path)
    second = open_database(path)
    Journal(first).append(make_event(0))
    Journal(second).append(make_event(1))
    assert Journal(first).count() == 2
    assert Journal(first).verify_chain() == []
    first.close()
    second.close()


# --- Crash recovery --------------------------------------------------------

_CRASH_SCRIPT = """
import os, sys
sys.path.insert(0, {src!r})
from provalume.store.db import open_database
from provalume.store.journal import Journal
from provalume.schemas.events import Event, EventType
from provalume.schemas.trust import Source

db = open_database({path!r})
journal = Journal(db)
for i in range(5):
    journal.append(Event.create(
        event_type=EventType.VERIFICATION_FAILED,
        project_id="p1",
        source=Source.KERNEL,
        payload={{"command": f"cmd-{{i}}", "exit_code": 1}},
    ))
# Die without closing: no COMMIT of anything in flight, no clean shutdown.
os._exit(9)
"""


def test_database_survives_a_hard_process_kill(tmp_path: Path) -> None:
    """SIGKILL-equivalent mid-run. WAL plus synchronous=NORMAL must leave a
    consistent database, which is the failure mode that actually matters."""
    path = tmp_path / "crash.db"
    src = str(Path(__file__).resolve().parents[2] / "src")
    script = _CRASH_SCRIPT.format(src=src, path=str(path))

    result = subprocess.run(  # noqa: S603 - runs this interpreter on a fixed script
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 9, result.stderr

    recovered = open_database(path)
    journal = Journal(recovered)
    assert journal.count() == 5, "committed events were lost across a hard kill"
    assert journal.verify_chain() == [], "the chain did not survive a hard kill"
    assert recovered.integrity_check() == []
    recovered.close()


def test_rollback_leaves_no_partial_state(file_db: Database) -> None:
    journal = Journal(file_db)
    journal.append(make_event(0))
    before = journal.head()

    def write_then_fail() -> None:
        with file_db.tx() as conn:
            conn.execute(
                "INSERT INTO projects (project_id, created_at) VALUES ('x', 'y')"
            )
            msg = "simulated failure mid-transaction"
            raise RuntimeError(msg)

    with pytest.raises(RuntimeError):
        write_then_fail()

    assert journal.head() == before
    assert file_db.query_one(
        "SELECT project_id FROM projects WHERE project_id = 'x'"
    ) is None


def test_nested_transactions_reuse_the_outer_one(file_db: Database) -> None:
    """A writer calling another writer must not need to know which is outermost."""
    with file_db.tx() as outer:
        outer.execute("INSERT INTO projects (project_id, created_at) VALUES ('a', 't')")
        with file_db.tx() as inner:
            inner.execute("INSERT INTO projects (project_id, created_at) VALUES ('b', 't')")
    assert file_db.scalar("SELECT COUNT(*) FROM projects") == 2


# --- Corruption detection --------------------------------------------------


def test_integrity_check_detects_a_corrupted_file(tmp_path: Path) -> None:
    """Corrupt real b-tree pages, not slack space.

    Writing one event leaves most of the file unused, and scribbling on unused
    bytes proves nothing — `integrity_check` correctly ignores them. Enough
    events to fill several pages, with the WAL checkpointed into the main file,
    is what makes this a real corruption test.
    """
    path = tmp_path / "corrupt.db"
    database = open_database(path)
    journal = Journal(database)
    for index in range(400):
        journal.append(make_event(index))
    # Fold the WAL into the main database so the corruption cannot be undone by
    # replaying a clean log over the damaged pages.
    database.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    database.close()

    data = bytearray(path.read_bytes())
    page_size = int.from_bytes(data[16:18], "big") or 4096
    assert len(data) > page_size * 4, "the database is too small to corrupt meaningfully"

    # Scribble over pages 3 onward, which hold table and index content. Page 1 is
    # the header and page 2 is typically the first table root; damaging those
    # tends to make the file unopenable rather than internally inconsistent.
    for page in range(2, min(6, len(data) // page_size)):
        start = page * page_size
        for offset in range(start, min(start + page_size, len(data))):
            data[offset] = 0xFF
    path.write_bytes(bytes(data))

    try:
        database = open_database(path)
    except (StoreError, sqlite3.DatabaseError):
        return  # refusing to open a corrupt file is an acceptable outcome
    with contextlib.closing(database):
        try:
            problems = database.integrity_check()
        except sqlite3.DatabaseError:
            return  # so is failing loudly during the check itself
    assert problems, "corruption of real data pages was not detected"


def test_verify_healthy_raises_on_foreign_key_violation(file_db: Database) -> None:
    from provalume.store.db import verify_healthy

    verify_healthy(file_db)
    # Insert a memory whose supersedes_id points nowhere, with FK enforcement
    # temporarily off, to simulate out-of-band tampering.
    file_db.execute("PRAGMA foreign_keys=OFF")
    with file_db.tx() as conn:
        conn.execute(
            "INSERT INTO memories (memory_id, schema_version, memory_type, content, "
            "text, scope_level, project_id, source, valid_at, recorded_at, "
            "supersedes_id, trust_state, verification_state, review_state, "
            "integration_state, content_hash) VALUES "
            "('m1', 1, 'semantic', '{}', 't', 'branch', 'p', 'kernel', 'x', 'x', "
            "'ghost', 'observed', 'unknown', 'none', 'none', 'h')"
        )
    file_db.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(IntegrityError, match="foreign key"):
        verify_healthy(file_db)


# --- Migrations ------------------------------------------------------------


def test_a_fresh_database_is_at_the_current_version(file_db: Database) -> None:
    assert file_db.schema_version() == SCHEMA_VERSION


def test_reopening_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "again.db"
    for _ in range(3):
        database = open_database(path)
        assert database.schema_version() == SCHEMA_VERSION
        database.close()


def test_a_newer_schema_is_refused(tmp_path: Path) -> None:
    """Operating on a schema whose semantics are unknown corrupts data quietly."""
    path = tmp_path / "future.db"
    database = open_database(path)
    database.close()

    raw = sqlite3.connect(path)
    # Interpolates an int constant, not caller input.
    raw.execute(f"UPDATE schema_version SET version = {SCHEMA_VERSION + 5}")  # noqa: S608
    raw.commit()
    raw.close()

    with pytest.raises(SchemaVersionError, match="newer than this build"):
        open_database(path)


def test_migration_preserves_existing_events(tmp_path: Path) -> None:
    path = tmp_path / "preserve.db"
    database = open_database(path)
    journal = Journal(database)
    for index in range(5):
        journal.append(make_event(index))
    head_before = journal.head()
    database.close()

    reopened = open_database(path)
    assert Journal(reopened).head() == head_before
    assert Journal(reopened).verify_chain() == []
    reopened.close()


def test_append_only_triggers_exist_after_migration(file_db: Database) -> None:
    rows = file_db.query(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'events_no_%'"
    )
    assert {str(r["name"]) for r in rows} == {"events_no_update", "events_no_delete"}


def test_fts_index_stays_in_step_with_memories(file_db: Database) -> None:
    """External-content FTS: the index must never disagree with the table, which
    would be a silent correctness bug."""
    from provalume.sdk.client import Provalume

    pv = Provalume(file_db, project_id="p", git=None)
    pv.record_verification(command="unique-token-xyz run", passed=False,
                           excerpt="E boom", error_kind="e")
    assert pv.recall("unique-token-xyz", limit=5).results

    pv.rebuild()
    assert pv.recall("unique-token-xyz", limit=5).results, (
        "the FTS index did not survive a projection rebuild"
    )


# --- Parallel access -------------------------------------------------------


def _append_in_process(path: str, count: int) -> int:
    database = open_database(path)
    journal = Journal(database)
    written = 0
    for index in range(count):
        # A writer that loses the race is the expected outcome under a busy
        # timeout, not an error. What matters is that the chain stays intact,
        # which the caller asserts.
        with contextlib.suppress(StoreError):
            journal.append(make_event(index))
            written += 1
    database.close()
    return written


@pytest.mark.slow
def test_concurrent_writers_do_not_corrupt_the_chain(tmp_path: Path) -> None:
    """Provalume is single-writer by design; concurrent writers must serialise on
    the busy timeout rather than interleave into a broken chain."""
    path = str(tmp_path / "parallel.db")
    open_database(path).close()  # migrate once up front

    context = multiprocessing.get_context("spawn")
    with context.Pool(3) as pool:
        written = pool.starmap(_append_in_process, [(path, 5)] * 3)

    database = open_database(path)
    journal = Journal(database)
    assert journal.count() == sum(written)
    assert journal.verify_chain() == [], "concurrent writes broke the hash chain"
    assert database.integrity_check() == []
    database.close()
