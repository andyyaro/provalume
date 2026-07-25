"""JSONL export/import, signatures, and audit behaviour.

The governing property: **an imported record is untrusted input.** Its claimed
trust state carries no weight, a record from the future is rejected rather than
partially interpreted, and a duplicate id with different content is a conflict
rather than an overwrite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from provalume.errors import SignatureError
from provalume.interchange import jsonl, signatures
from provalume.interchange.hashing import canonical_json, hash_payload
from provalume.schemas.trust import Source, TrustState
from provalume.sdk.client import Provalume


@pytest.fixture
def exported(pv: Provalume, tmp_path: Path) -> tuple[Provalume, Path]:
    pv.record_verification(command="pytest -q", passed=False,
                           excerpt="E AssertionError: boom", error_kind="test_failure",
                           task_id="t1")
    pv.record_verification(command="pytest -q tests/unit", passed=True, task_id="t1")
    pv.record_decision(selected="split the suite", rejected=["raise the timeout"])
    out = tmp_path / "export"
    pv.export(out)
    return pv, out


# --- Export ----------------------------------------------------------------


def test_export_writes_all_three_files(exported: tuple[Provalume, Path]) -> None:
    _, out = exported
    for name in (jsonl.EVENTS_FILE, jsonl.MEMORIES_FILE, jsonl.TRANSITIONS_FILE):
        assert (out / name).exists()


def test_export_is_byte_identical_across_runs(exported: tuple[Provalume, Path]) -> None:
    pv, out = exported
    first = (out / jsonl.EVENTS_FILE).read_bytes()
    pv.export(out)
    assert (out / jsonl.EVENTS_FILE).read_bytes() == first


def test_records_are_sorted_by_kind_and_id(exported: tuple[Provalume, Path]) -> None:
    """Sorting by ID is what makes two machines produce comparable files."""
    _, out = exported
    ids = [
        json.loads(line)["id"]
        for line in (out / jsonl.EVENTS_FILE).read_text().splitlines()
    ]
    assert ids == sorted(ids)


def test_local_chain_fields_are_not_exported(exported: tuple[Provalume, Path]) -> None:
    """They describe this database's chain, not the record. Exporting them would
    invite an importer to treat a foreign chain as its own."""
    _, out = exported
    for line in (out / jsonl.EVENTS_FILE).read_text().splitlines():
        record = json.loads(line)
        for field in ("seq", "event_hash", "prev_event_hash"):
            assert field not in record


def test_every_record_declares_its_version(exported: tuple[Provalume, Path]) -> None:
    _, out = exported
    for name in (jsonl.EVENTS_FILE, jsonl.MEMORIES_FILE):
        for line in (out / name).read_text().splitlines():
            record = json.loads(line)
            assert record["rv"] == jsonl.RECORD_VERSION
            assert record["kind"]


def test_absent_optionals_are_omitted_not_nulled(exported: tuple[Provalume, Path]) -> None:
    _, out = exported
    for line in (out / jsonl.EVENTS_FILE).read_text().splitlines():
        record = json.loads(line)
        assert None not in record.values()


# --- Import ----------------------------------------------------------------


def test_reimport_is_idempotent(exported: tuple[Provalume, Path]) -> None:
    pv, out = exported
    result = pv.import_records(out)
    assert result.ok
    assert result.skipped_duplicates > 0
    assert not result.conflicts


def test_import_into_a_fresh_database_reconstructs_memory(
    exported: tuple[Provalume, Path], tmp_path: Path
) -> None:
    _, out = exported
    from provalume.store.db import open_database

    fresh = Provalume(open_database(":memory:"), project_id="test-project", git=None)
    result = fresh.import_records(out)
    assert result.events
    assert result.ok
    fresh.rebuild()
    assert fresh.memory_records(include_terminal=True, current_only=False, limit=50)


def test_imported_records_are_never_trusted_on_arrival(
    exported: tuple[Provalume, Path]
) -> None:
    """A record's *claimed* trust state in a file carries no weight (T17)."""
    _, out = exported
    records = [
        json.loads(line) for line in (out / jsonl.MEMORIES_FILE).read_text().splitlines()
    ]
    assert any(r["trust_state"] != "quarantined" for r in records), (
        "the fixture should contain a trusted record for this test to mean anything"
    )
    for record in records:
        memory = jsonl.record_to_memory(record)
        assert memory.trust_state is TrustState.QUARANTINED
        assert memory.source is Source.IMPORT


def test_imported_events_are_marked_as_imported(exported: tuple[Provalume, Path]) -> None:
    _, out = exported
    line = (out / jsonl.EVENTS_FILE).read_text().splitlines()[0]
    record = json.loads(line)
    record["source"] = "kernel"  # a file claiming to be kernel-sourced
    assert jsonl.record_to_event(record).source is Source.IMPORT


def tamper_payload(directory: Path, *, key: str, value: object) -> dict:
    """Alter one event's payload in place, leaving its declared hash stale.

    Picks the record deterministically rather than taking ``lines[0]``: records
    are sorted by ``(kind, id)`` and IDs are time-ordered, so which event lands
    first depends on sub-millisecond timing. An earlier version of this test
    string-replaced a marker in the first line and passed locally while failing
    on one CI runner, because that line happened to be a different event and the
    replacement was a no-op.
    """
    path = directory / jsonl.EVENTS_FILE
    records = [json.loads(line) for line in path.read_text().splitlines()]
    target = next(r for r in records if r["event_type"] == "verification.failed")
    target["payload"][key] = value
    path.write_text(canonical_json(target) + "\n")
    return target


def test_a_tampered_payload_with_a_stale_hash_is_rejected(
    exported: tuple[Provalume, Path]
) -> None:
    """The forgery shape a trusting importer would wave through as a duplicate."""
    pv, out = exported
    # Payload changed, declared hash left untouched — the whole point.
    tamper_payload(out, key="excerpt", value="E TamperedError: forged")

    result = pv.import_records(out, apply=False)
    assert result.rejected
    assert any("payload_hash" in str(i) for i in result.rejected)


def test_a_genuine_divergence_is_a_conflict_not_an_overwrite(
    exported: tuple[Provalume, Path]
) -> None:
    """Content genuinely differs and the hash was updated to match: a real
    divergence between two machines, not a forgery. Must be a conflict rather
    than a silent overwrite."""
    pv, out = exported
    record = tamper_payload(out, key="excerpt", value="E AssertionError: something else")
    record["payload_hash"] = hash_payload(record["payload"])
    (out / jsonl.EVENTS_FILE).write_text(canonical_json(record) + "\n")

    result = pv.import_records(out, apply=False)
    assert result.conflicts
    assert not result.ok


def test_a_future_record_version_is_rejected(exported: tuple[Provalume, Path]) -> None:
    """Never partially interpreted: a record from the future cannot be validated."""
    pv, out = exported
    record = json.loads(sorted((out / jsonl.EVENTS_FILE).read_text().splitlines())[0])
    record["rv"] = jsonl.RECORD_VERSION + 99
    (out / jsonl.EVENTS_FILE).write_text(canonical_json(record) + "\n")

    result = pv.import_records(out, apply=False)
    assert result.rejected
    assert any("newer than this build" in str(i) for i in result.rejected)


def test_a_future_version_can_be_quarantined_instead(
    exported: tuple[Provalume, Path]
) -> None:
    pv, out = exported
    record = json.loads((out / jsonl.EVENTS_FILE).read_text().splitlines()[0])
    record["rv"] = jsonl.RECORD_VERSION + 99
    (out / jsonl.EVENTS_FILE).write_text(canonical_json(record) + "\n")

    result = pv.import_records(out, apply=False, quarantine_unknown=True)
    assert result.quarantined
    assert not result.rejected


def test_a_foreign_project_is_rejected_by_default(
    exported: tuple[Provalume, Path]
) -> None:
    pv, out = exported
    record = json.loads((out / jsonl.EVENTS_FILE).read_text().splitlines()[0])
    record["project_id"] = "someone-elses-project"
    record["payload_hash"] = hash_payload(record["payload"])
    (out / jsonl.EVENTS_FILE).write_text(canonical_json(record) + "\n")

    blocked = pv.import_records(out, apply=False)
    assert blocked.rejected
    assert any("belongs to project" in str(i) for i in blocked.rejected)

    allowed = pv.import_records(out, apply=False, allow_foreign_project=True)
    assert not allowed.rejected


def test_malformed_lines_are_reported_and_the_file_continues(
    exported: tuple[Provalume, Path]
) -> None:
    pv, out = exported
    good = (out / jsonl.EVENTS_FILE).read_text().splitlines()
    (out / jsonl.EVENTS_FILE).write_text("{not json\n" + "\n".join(good) + "\n")

    result = pv.import_records(out, apply=False)
    assert result.rejected
    assert result.skipped_duplicates > 0, "one bad line aborted the whole file"


def test_an_oversized_line_is_rejected_without_aborting(
    exported: tuple[Provalume, Path]
) -> None:
    pv, out = exported
    huge = json.dumps({"rv": 1, "kind": "event", "id": "X", "pad": "x" * (2 * 1024 * 1024)})
    existing = (out / jsonl.EVENTS_FILE).read_text()
    (out / jsonl.EVENTS_FILE).write_text(huge + "\n" + existing)

    result = pv.import_records(out, apply=False)
    assert any("size cap" in str(i) for i in result.rejected)


def test_divergent_supersession_is_surfaced_not_resolved(
    pv: Provalume, tmp_path: Path
) -> None:
    """Auto-resolving would be auto-deciding which contributor was right."""
    pv.record_fact(subject="pm", statement="Uses pip.")
    original = pv.memory_records(limit=5)[0]
    out = tmp_path / "export"
    pv.export(out)

    lines = (out / jsonl.MEMORIES_FILE).read_text().splitlines()
    record = json.loads(lines[0])
    rivals = []
    for suffix in ("AAA", "BBB"):
        rival = dict(record)
        rival["id"] = (record["id"][:-3] + suffix)
        rival["supersedes_id"] = original.memory_id
        rivals.append(canonical_json(rival))
    (out / jsonl.MEMORIES_FILE).write_text("\n".join([*lines, *rivals]) + "\n")

    result = pv.import_records(out, apply=False)
    assert any("divergent supersession" in str(c) for c in result.conflicts)


def test_import_summary_is_readable(exported: tuple[Provalume, Path]) -> None:
    pv, out = exported
    summary = jsonl.summarize(pv.import_records(out, apply=False))
    assert "accepted:" in summary
    assert "duplicates:" in summary


def test_importing_a_missing_directory_raises(pv: Provalume, tmp_path: Path) -> None:
    from provalume.errors import InterchangeError

    with pytest.raises(InterchangeError, match="not a directory"):
        pv.import_records(tmp_path / "does-not-exist")


# --- Signatures ------------------------------------------------------------


def test_hmac_round_trip() -> None:
    record = {"rv": 1, "kind": "event", "id": "E1", "payload": {"a": 1}}
    key = b"shared-secret"
    signature = signatures.sign_hmac(record, key=key, key_id="team")
    assert signatures.verify_hmac(record, signature, key=key)


def test_hmac_detects_tampering() -> None:
    record = {"rv": 1, "id": "E1", "payload": {"a": 1}}
    key = b"shared-secret"
    signature = signatures.sign_hmac(record, key=key, key_id="team")
    record["payload"] = {"a": 2}
    assert not signatures.verify_hmac(record, signature, key=key)


def test_hmac_rejects_a_different_key() -> None:
    record = {"rv": 1, "id": "E1"}
    signature = signatures.sign_hmac(record, key=b"one", key_id="k")
    assert not signatures.verify_hmac(record, signature, key=b"two")


def test_verifier_requires_a_pinned_key() -> None:
    """A record carrying its own key would be self-authenticating, which is not
    authentication at all."""
    record = {"rv": 1, "id": "E1"}
    record["signature"] = signatures.sign_hmac(record, key=b"k", key_id="unknown-signer")
    ok, reason = signatures.Verifier(hmac_keys={}).verify(record)
    assert not ok
    assert "unknown signer" in reason


def test_verifier_accepts_a_pinned_key() -> None:
    record: dict = {"rv": 1, "id": "E1"}
    record["signature"] = signatures.sign_hmac(record, key=b"k", key_id="team")
    ok, reason = signatures.Verifier(hmac_keys={"team": b"k"}).verify(record)
    assert ok
    assert "valid" in reason


def test_unsigned_records_pass_unless_signatures_are_required() -> None:
    assert signatures.Verifier().verify({"rv": 1})[0]
    ok, reason = signatures.Verifier(require_signature=True).verify({"rv": 1})
    assert not ok
    assert "unsigned" in reason


def test_an_unsupported_scheme_is_refused() -> None:
    ok, reason = signatures.Verifier().verify(
        {"rv": 1, "signature": {"scheme": "magic", "value": "x"}}
    )
    assert not ok
    assert "unsupported" in reason


def test_a_malformed_signature_field_is_refused() -> None:
    ok, _ = signatures.Verifier().verify({"rv": 1, "signature": "not-an-object"})
    assert not ok


@pytest.mark.signatures
def test_ed25519_round_trip() -> None:
    if not signatures.ed25519_available():
        pytest.skip("the signatures extra is not installed")
    private, public = signatures.generate_ed25519_keypair()
    record: dict = {"rv": 1, "id": "E1", "payload": {"a": 1}}
    record["signature"] = signatures.sign_ed25519(record, private_key=private, key_id="me")
    ok, _ = signatures.Verifier(ed25519_keys={"me": public}).verify(record)
    assert ok


@pytest.mark.signatures
def test_ed25519_detects_tampering() -> None:
    if not signatures.ed25519_available():
        pytest.skip("the signatures extra is not installed")
    private, public = signatures.generate_ed25519_keypair()
    record: dict = {"rv": 1, "id": "E1", "payload": {"a": 1}}
    signature = signatures.sign_ed25519(record, private_key=private)
    record["payload"] = {"a": 999}
    assert not signatures.verify_ed25519(record, signature, public_key=public)


def test_verification_without_the_backend_raises_rather_than_returning_false() -> None:
    """"Invalid" and "I cannot check this" are different facts; collapsing them
    would let a missing dependency read as a forgery, or get skipped."""
    if signatures.ed25519_available():
        pytest.skip("the backend is installed, so this path cannot be exercised")
    with pytest.raises(SignatureError, match="not installed"):
        signatures.verify_ed25519(
            {"rv": 1}, {"scheme": "ed25519", "value": "00"}, public_key=b"\x00" * 32
        )


def test_signed_export_import_round_trip(pv: Provalume, tmp_path: Path) -> None:
    pv.record_verification(command="pytest", passed=True)
    out = tmp_path / "signed"
    key = b"team-secret"
    pv.export(out, signer=lambda r: signatures.sign_hmac(r, key=key, key_id="team"))

    verifier = signatures.Verifier(hmac_keys={"team": key})
    result = pv.import_records(out, apply=False, verifier=verifier)
    assert not result.quarantined, [str(q) for q in result.quarantined]


def test_a_bad_signature_quarantines_rather_than_accepting(
    pv: Provalume, tmp_path: Path
) -> None:
    pv.record_verification(command="pytest", passed=True)
    out = tmp_path / "signed"
    pv.export(out, signer=lambda r: signatures.sign_hmac(r, key=b"real", key_id="team"))

    wrong = signatures.Verifier(hmac_keys={"team": b"wrong"})
    result = pv.import_records(out, apply=False, verifier=wrong)
    assert result.quarantined
    assert all("signature" in str(q) for q in result.quarantined)
