"""The append-only event journal."""

from __future__ import annotations

import pytest

from provalume.errors import AppendOnlyViolation, IntegrityError
from provalume.schemas.events import Event, EventFilter, EventType
from provalume.schemas.trust import Source
from provalume.store.db import Database
from provalume.store.journal import Journal


def make_event(**overrides: object) -> Event:
    defaults: dict = {
        "event_type": EventType.VERIFICATION_FAILED,
        "project_id": "p1",
        "source": Source.KERNEL,
        "payload": {"command": "pytest", "exit_code": 1},
    }
    defaults.update(overrides)
    return Event.create(**defaults)  # type: ignore[arg-type]


def test_append_assigns_hashes_and_sequence(journal: Journal) -> None:
    result = journal.append(make_event())
    assert result.inserted
    assert result.seq == 1
    assert result.event.payload_hash.startswith("sha256:")
    assert result.event.event_hash.startswith("sha256:")
    assert result.event.prev_event_hash == "", "the first event has no predecessor"


def test_chain_links_each_event_to_its_predecessor(journal: Journal) -> None:
    first = journal.append(make_event())
    second = journal.append(make_event())
    assert second.event.prev_event_hash == first.event.event_hash
    assert journal.verify_chain() == []


def test_head_tracks_the_latest_event(journal: Journal) -> None:
    assert journal.head() == ("", 0, 0)
    journal.append(make_event())
    last = journal.append(make_event())
    head_hash, seq, count = journal.head()
    assert head_hash == last.event.event_hash
    assert seq == 2
    assert count == 2


def test_reappending_identical_content_is_idempotent(journal: Journal) -> None:
    event = make_event()
    first = journal.append(event)
    second = journal.append(first.event)
    assert not second.inserted
    assert second.seq == first.seq
    assert journal.count() == 1


def test_reappending_same_id_with_different_content_is_refused(journal: Journal) -> None:
    """Accepting this would let an importer rewrite history through the one
    path that is supposed to be append-only."""
    stored = journal.append(make_event()).event
    tampered = stored.model_copy(update={"payload": {"command": "TAMPERED"}})
    with pytest.raises(IntegrityError, match="different content"):
        journal.append(tampered)


def test_update_is_blocked_by_the_database(db: Database, journal: Journal) -> None:
    """Enforced by a trigger, not by convention: the threat model includes
    someone opening the file with sqlite3."""
    journal.append(make_event())
    with pytest.raises(AppendOnlyViolation):
        db.execute("UPDATE events SET payload = '{}' WHERE seq = 1")


def test_delete_is_blocked_by_the_database(db: Database, journal: Journal) -> None:
    journal.append(make_event())
    with pytest.raises(AppendOnlyViolation):
        db.execute("DELETE FROM events WHERE seq = 1")


def test_chain_verification_detects_a_tampered_payload(db: Database, journal: Journal) -> None:
    """Direct row surgery must be visible to audit, even though the triggers
    block the ordinary paths."""
    journal.append(make_event())
    # Drop the triggers to simulate an attacker who can write the raw file.
    db.execute("DROP TRIGGER events_no_update")
    db.execute('UPDATE events SET payload = \'{"command":"evil"}\' WHERE seq = 1')
    problems = journal.verify_chain()
    assert problems, "a tampered payload was not detected"
    assert any("payload hash mismatch" in p for p in problems)


def test_chain_verification_detects_a_removed_event(db: Database, journal: Journal) -> None:
    journal.append(make_event())
    journal.append(make_event())
    db.execute("DROP TRIGGER events_no_delete")
    db.execute("DELETE FROM events WHERE seq = 1")
    problems = journal.verify_chain()
    assert problems, "a removed event was not detected"


def test_get_returns_none_for_unknown_id(journal: Journal) -> None:
    assert journal.get("does-not-exist") is None


def test_get_many_preserves_sequence_order(journal: Journal) -> None:
    ids = [journal.append(make_event()).event.event_id for _ in range(3)]
    found = journal.get_many(tuple(reversed(ids)))
    assert [e.event_id for e in found] == ids


def test_find_filters_by_type_and_source(journal: Journal) -> None:
    journal.append(make_event(event_type=EventType.VERIFICATION_PASSED))
    journal.append(make_event(event_type=EventType.VERIFICATION_FAILED))
    journal.append(make_event(source=Source.AGENT, event_type=EventType.AGENT_PROPOSAL))

    passed = journal.find(
        EventFilter(project_id="p1", event_types=(EventType.VERIFICATION_PASSED,))
    )
    assert len(passed) == 1

    agent = journal.find(EventFilter(project_id="p1", sources=(Source.AGENT,)))
    assert len(agent) == 1


def test_find_isolates_projects(journal: Journal) -> None:
    journal.append(make_event(project_id="a"))
    journal.append(make_event(project_id="b"))
    assert len(journal.find(EventFilter(project_id="a"))) == 1


def test_iter_all_streams_in_chain_order(journal: Journal) -> None:
    for _ in range(25):
        journal.append(make_event())
    seqs = [e.seq for e in journal.iter_all(batch=10)]
    assert seqs == sorted(seqs)
    assert len(seqs) == 25


def test_append_many_is_atomic(journal: Journal) -> None:
    events = [make_event() for _ in range(3)]
    results = journal.append_many(events)
    assert all(r.inserted for r in results)
    assert journal.count() == 3
    assert journal.verify_chain() == []


def test_commit_sha_must_be_hexadecimal() -> None:
    """A non-SHA commit cannot be resolved against a repository, so accepting
    one would produce provenance that looks checkable and is not."""
    with pytest.raises(ValueError, match="hexadecimal"):
        make_event(commit_sha="not-a-sha!")


def test_commit_sha_is_normalised_to_lowercase() -> None:
    event = make_event(commit_sha="ABCDEF1234")
    assert event.commit_sha == "abcdef1234"


def test_timestamps_are_canonicalised() -> None:
    event = make_event(recorded_at="2026-07-25T10:00:00+02:00")
    assert event.recorded_at == "2026-07-25T08:00:00.000Z"


def test_duplicate_payload_detection(journal: Journal) -> None:
    """Identical payloads are legitimate — the same command failing twice — but
    they are also the signature of a re-imported export, so they are surfaced."""
    journal.append(make_event())
    journal.append(make_event())
    duplicates = journal.duplicate_payloads()
    assert duplicates and duplicates[0][1] == 2
