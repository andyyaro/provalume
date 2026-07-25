"""What a resolution attaches to, and what a reviewer's subject reaches.

Two mechanisms that the docstrings describe and the code did not deliver:

* the declared cross-run path (``resolves_signature``) overwrote a resolution
  that was already recorded, so a later unrelated success became the answer the
  preflight gate serves as ``what_later_worked`` — while the rendered text went
  on naming the real fix — and it never wrote the link that
  ``Provenance.resolves_gotcha_id`` is read from;
* a reviewer's later approval of the same subject never reached a lesson,
  because a lesson keys on subject *and* finding together while the lookup was
  built from the subject alone. The two agree only for a rejection that stated
  no reason, which is the case ``build_lesson`` exists to avoid.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from provalume.schemas.memories import Memory, MemoryFilter, MemoryType

if TYPE_CHECKING:
    from provalume.sdk.client import Provalume

FAILING = "pytest -n auto tests/integration"
REAL_FIX = "pytest -p no:xdist tests/integration"
SUBJECT = "connection pool sizing"
FINDING = "the pool is sized from a constant instead of the config value"


def _gotchas(pv: Provalume) -> list[Memory]:
    return pv.memory_records(
        MemoryFilter(
            project_id=pv.project_id,
            memory_types=(MemoryType.GOTCHA,),
            include_terminal=True,
            current_only=False,
            limit=10,
        )
    )


def _the_gotcha(pv: Provalume) -> Memory:
    found = _gotchas(pv)
    assert found, "no gotcha was projected"
    assert len(found) == 1, f"expected one gotcha, got {len(found)}"
    return found[0]


def _signature(pv: Provalume) -> str:
    rows = pv.memories.all_signatures(pv.project_id)
    assert rows, "no failure signature was recorded"
    return str(rows[0]["signature"])


def _record_failure(pv: Provalume) -> None:
    pv.record_verification(
        command=FAILING,
        passed=False,
        excerpt="E TimeoutError: deadlock in db fixture teardown",
        error_kind="test_failure",
        run_id="run-1",
        task_id="t1",
    )


def test_a_later_success_does_not_overwrite_a_recorded_resolution(pv: Provalume) -> None:
    """The first real fix is the answer; a second claim does not replace it."""
    _record_failure(pv)
    signature = _signature(pv)

    pv.record_verification(
        command=REAL_FIX, passed=True, resolves_signature=signature, run_id="run-2", task_id="t2"
    )
    pv.record_verification(
        command="echo hello",
        passed=True,
        resolves_signature=signature,
        run_id="run-3",
        task_id="t3",
    )

    resolution = _the_gotcha(pv).content["resolution"]
    assert resolution["command"] == REAL_FIX, (
        f"the gate would serve {resolution['command']!r} as what later worked"
    )


def test_the_signature_row_keeps_pointing_at_the_real_fix(pv: Provalume) -> None:
    """`resolved_by_id` is repointed by the same write, so pin it too."""
    _record_failure(pv)
    signature = _signature(pv)

    fix = pv.record_verification(
        command=REAL_FIX, passed=True, resolves_signature=signature, run_id="run-2", task_id="t2"
    )
    pv.record_verification(
        command="echo hello",
        passed=True,
        resolves_signature=signature,
        run_id="run-3",
        task_id="t3",
    )

    rows = pv.memories.signature_rows(pv.project_id, signature)
    assert rows[0]["resolved_by_id"] == fix.event_id


def test_the_fix_records_which_failure_it_resolved(pv: Provalume) -> None:
    """The link runs fix → failure, which is the direction the field claims."""
    _record_failure(pv)
    gotcha_id = _the_gotcha(pv).memory_id

    pv.record_verification(
        command=REAL_FIX,
        passed=True,
        resolves_signature=_signature(pv),
        run_id="run-2",
        task_id="t2",
    )

    procedures = pv.memory_records(
        MemoryFilter(
            project_id=pv.project_id,
            memory_types=(MemoryType.PROCEDURAL,),
            include_terminal=True,
            current_only=False,
            limit=10,
        )
    )
    fix = next(m for m in procedures if m.content["command"] == REAL_FIX)
    links = pv.memories.links_from(fix.memory_id, link_type="resolved_by")
    assert [link["to_id"] for link in links] == [gotcha_id], (
        "the record that documents the fix does not name the failure it resolved"
    )
    assert not pv.memories.links_from(gotcha_id, link_type="resolved_by")


def test_the_in_run_link_runs_the_same_way(pv: Provalume) -> None:
    """The inferred path writes the same direction as the declared one."""
    pv.record_verification(
        command=FAILING,
        passed=False,
        excerpt="E TimeoutError",
        error_kind="test_failure",
        purpose="the integration suite",
        task_id="t1",
    )
    gotcha_id = _the_gotcha(pv).memory_id
    pv.record_verification(
        command=REAL_FIX, passed=True, purpose="the integration suite", task_id="t1"
    )

    fix = next(
        m
        for m in pv.memory_records(
            MemoryFilter(
                project_id=pv.project_id,
                memory_types=(MemoryType.PROCEDURAL,),
                include_terminal=True,
                current_only=False,
                limit=10,
            )
        )
        if m.content["command"] == REAL_FIX
    )
    links = pv.memories.links_from(fix.memory_id, link_type="resolved_by")
    assert [link["to_id"] for link in links] == [gotcha_id]


def test_an_approval_resolves_the_lesson_that_stated_a_reason(pv: Provalume) -> None:
    """A rejection with a finding is the case the mechanism exists for."""
    pv.record_review(
        reviewer="rev-1", approved=False, subject=SUBJECT, finding=FINDING, task_id="t1"
    )
    pv.record_review(reviewer="rev-2", approved=True, subject=SUBJECT, task_id="t2")

    lesson = _the_gotcha(pv)
    resolution = lesson.content["resolution"]
    assert resolution is not None, (
        "the lesson still reads as unresolved after the subject was approved"
    )
    assert resolution["reviewer"] == "rev-2"
    assert "Later approved by rev-2" in lesson.text


def test_an_approval_of_another_subject_resolves_nothing(pv: Provalume) -> None:
    """The negative twin: matching on the subject must stay a match, not a scan."""
    pv.record_review(
        reviewer="rev-1", approved=False, subject=SUBJECT, finding=FINDING, task_id="t1"
    )
    pv.record_review(
        reviewer="rev-2", approved=True, subject="log rotation policy", task_id="t2"
    )

    assert _the_gotcha(pv).content["resolution"] is None


def test_resolution_survives_a_rebuild(pv: Provalume) -> None:
    """Both paths are projections of the journal, not live-path state."""
    _record_failure(pv)
    pv.record_verification(
        command=REAL_FIX,
        passed=True,
        resolves_signature=_signature(pv),
        run_id="run-2",
        task_id="t2",
    )
    pv.record_review(
        reviewer="rev-1", approved=False, subject=SUBJECT, finding=FINDING, task_id="t3"
    )
    pv.record_review(reviewer="rev-2", approved=True, subject=SUBJECT, task_id="t4")
    before = {m.memory_id: m.content["resolution"] for m in _gotchas(pv)}

    pv.rebuild()

    assert {m.memory_id: m.content["resolution"] for m in _gotchas(pv)} == before
