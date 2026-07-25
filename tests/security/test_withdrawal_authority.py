"""Withdrawing a memory needs authority, exactly as promoting one does.

Found by dogfooding. `human.rejection`, `human.invalidation` and
`branch.rejected` acted on whatever their payload named, with no check on the
event's source and no check that the memory belonged to the event's project.
`record_to_event` forces `source=import` so that a file cannot promote itself
(threat T17) — but that defence only covered the upward direction, and
`rejected` is terminal (`promotion.REFUSE_REJECTED`), so a two-line export could
permanently withdraw the recipient's own verified memories with no error, no
transition, and no way back.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from provalume.interchange import jsonl
from provalume.interchange.hashing import canonical_json, hash_payload
from provalume.schemas.memories import MemoryType
from provalume.schemas.trust import TrustState

if TYPE_CHECKING:
    from provalume.sdk.client import Provalume


def _share(directory: Path, records: list[tuple[str, dict]]) -> Path:
    """A hand-written export directory holding the given event records."""
    directory.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for index, (event_type, payload) in enumerate(records):
        record = {
            "rv": jsonl.RECORD_VERSION,
            "kind": jsonl.KIND_EVENT,
            "id": f"01HZZZZZZZZZZZZZZZZZZZZZ{index:02d}",
            "schema_version": 1,
            "event_type": event_type,
            "recorded_at": f"2026-01-01T00:00:0{index}.000Z",
            "project_id": "test-project",
            "source": "human",  # the claim the file makes about itself
            "payload": payload,
            "payload_hash": hash_payload(payload),
        }
        lines.append(canonical_json(record))
    (directory / jsonl.EVENTS_FILE).write_text("\n".join(lines) + "\n")
    return directory


def _victim(pv: Provalume) -> str:
    pv.record_verification(
        command="pytest -q", passed=True, purpose="the suite", branch="main", task_id="t1"
    )
    procedural = pv.memory_records(memory_types=[MemoryType.PROCEDURAL], limit=5)[0]
    assert procedural.trust_state is TrustState.VERIFIED
    return procedural.memory_id


def _state(pv: Provalume, memory_id: str) -> TrustState:
    memory = pv.memories.get(memory_id)
    assert memory is not None
    return memory.trust_state


def test_an_imported_rejection_cannot_withdraw_a_local_memory(
    pv: Provalume, tmp_path: Path
) -> None:
    memory_id = _victim(pv)
    share = _share(
        tmp_path / "share", [("human.rejection", {"memory_id": memory_id, "reason": "no"})]
    )

    result = pv.import_records(share)
    assert result.events, f"the rejection did not import: {result.rejected}"

    assert _state(pv, memory_id) is TrustState.VERIFIED, (
        "an imported file rejected the recipient's own verified memory"
    )
    assert any("may withdraw" in note for note in pv.rebuild().notes), (
        "the refusal left no trace at all"
    )


def test_an_imported_branch_rejection_cannot_wipe_a_local_branch(
    pv: Provalume, tmp_path: Path
) -> None:
    """The wider blast radius: one record withdraws everything on the branch."""
    memory_id = _victim(pv)
    share = _share(
        tmp_path / "share", [("branch.rejected", {"branch": "main", "reason": "abandoned"})]
    )

    result = pv.import_records(share)
    assert result.events, f"the rejection did not import: {result.rejected}"

    assert _state(pv, memory_id) is TrustState.VERIFIED
    withdrawn = [
        m
        for m in pv.memory_records(include_terminal=True, current_only=False, limit=50)
        if m.trust_state is TrustState.REJECTED
    ]
    assert not withdrawn, f"an imported file rejected {len(withdrawn)} local record(s)"


def test_an_imported_invalidation_cannot_withdraw_a_local_memory(
    pv: Provalume, tmp_path: Path
) -> None:
    memory_id = _victim(pv)
    share = _share(
        tmp_path / "share", [("human.invalidation", {"memory_id": memory_id, "reason": "no"})]
    )

    assert pv.import_records(share).events
    assert _state(pv, memory_id) is TrustState.VERIFIED


def test_one_project_cannot_withdraw_another_projects_memory(pv: Provalume) -> None:
    """`project_id` is the isolation boundary (T9), and `get` is by id alone."""
    from provalume.sdk.client import Provalume as Client

    memory_id = _victim(pv)
    beta = Client(pv.db, project_id="beta", git=None)
    beta.reject(memory_id, actor="beta-operator", reason="not mine to reject")

    assert _state(pv, memory_id) is TrustState.VERIFIED, (
        "project 'beta' rejected project 'test-project''s record"
    )
    assert any("belongs to project" in note for note in beta.rebuild().notes)


def test_a_genuine_human_rejection_still_withdraws(pv: Provalume) -> None:
    """The gate must not have closed the door it exists to guard."""
    memory_id = _victim(pv)
    pv.reject(memory_id, actor="tech-lead", reason="the procedure was wrong")
    assert _state(pv, memory_id) is TrustState.REJECTED
