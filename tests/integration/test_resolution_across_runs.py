"""Resolution linking when the fix lands in a later run.

Found by dogfooding. Orkestra's real recovery path is to block a task, escalate
to a human, and do the work in a *new* run — so the failure and its fix land in
different runs with different task ids. Inference scoped to a single task or run
therefore never fires on the path that matters, and `resolves_signature` — the
mechanism that does work across runs — had no writer at all: the projector read
it and nothing in the SDK could set it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from provalume import Provalume

if TYPE_CHECKING:
    from pathlib import Path

COMMAND = "pytest -q tests/"
FAILURE = "E   ConnectionError: transient upstream reset"


def _gotcha(pv: Provalume) -> dict:
    from provalume.schemas.memories import MemoryFilter, MemoryType

    found = pv.memory_records(
        MemoryFilter(
            project_id=pv.project_id,
            memory_types=(MemoryType.GOTCHA,),
            include_terminal=True,
            current_only=False,
            limit=10,
        )
    )
    assert found, "no gotcha was recorded"
    return dict(found[0].content)


def test_a_success_in_a_later_run_resolves_the_failure(tmp_path: Path) -> None:
    pv = Provalume.open(tmp_path / "m.db", project_id="p", use_git=False)

    pv.record_verification(command=COMMAND, passed=False, excerpt=FAILURE, task_id="task-A")
    signature = _gotcha(pv)["failure_signature"]
    assert signature, "the failure produced no signature"

    # A different run, a different task — as Orkestra actually behaves.
    pv.record_verification(
        command=COMMAND, passed=True, task_id="task-B", resolves_signature=signature
    )

    content = _gotcha(pv)
    assert content["resolution"] is not None, (
        "a success naming the signature did not resolve the failure"
    )
    pv.close()


def test_without_the_signature_a_later_run_does_not_link(tmp_path: Path) -> None:
    """The scoping that made this invisible, pinned so the gap stays visible."""
    pv = Provalume.open(tmp_path / "m.db", project_id="p", use_git=False)

    pv.record_verification(command=COMMAND, passed=False, excerpt=FAILURE, task_id="task-A")
    pv.record_verification(command=COMMAND, passed=True, task_id="task-B")

    assert _gotcha(pv)["resolution"] is None, (
        "cross-run inference now works without an explicit signature — if this is "
        "intended, the integration no longer needs to supply one"
    )
    pv.close()


def test_a_resolved_failure_is_not_announced_as_an_open_one(tmp_path: Path) -> None:
    """A resolved trap and an open one must not read the same."""
    pv = Provalume.open(tmp_path / "m.db", project_id="p", use_git=False)

    pv.record_verification(command=COMMAND, passed=False, excerpt=FAILURE, task_id="task-A")
    open_summary = pv.preflight(command=COMMAND, record=False).summary

    signature = _gotcha(pv)["failure_signature"]
    pv.record_verification(
        command=COMMAND, passed=True, task_id="task-B", resolves_signature=signature
    )
    resolved_summary = pv.preflight(command=COMMAND, record=False).summary

    assert "resolved" not in open_summary.lower().split("\n")[0]
    assert "resolved" in resolved_summary.lower().split("\n")[0], (
        f"a resolved failure still reads as open: {resolved_summary.splitlines()[0]!r}"
    )
    pv.close()


def test_a_lookup_can_consult_the_gate_without_recording_a_warning(tmp_path: Path) -> None:
    """`warning.shown` means a warning was put in front of someone.

    Emitting it for an internal lookup inflates the very count that warning
    usefulness is measured from.
    """
    from provalume.schemas.events import EventFilter, EventType

    pv = Provalume.open(tmp_path / "m.db", project_id="p", use_git=False)
    pv.record_verification(command=COMMAND, passed=False, excerpt=FAILURE, task_id="task-A")

    def warnings() -> int:
        return len(
            pv.events(
                EventFilter(
                    project_id=pv.project_id,
                    event_types=(EventType.WARNING_SHOWN,),
                    limit=100,
                )
            )
        )

    before = warnings()
    pv.preflight(command=COMMAND, record=False)
    assert warnings() == before, "a non-recording lookup still wrote a warning event"

    pv.preflight(command=COMMAND, record=True)
    assert warnings() == before + 1, "a recording check did not write a warning event"
    pv.close()
