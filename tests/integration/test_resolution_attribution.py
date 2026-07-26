"""A success may only resolve a failure it is actually linked to.

Found by dogfooding. `_resolve_matching_failures` claimed to match on purpose
but read neither the command nor the purpose: it attached a success to *every*
unresolved gotcha sharing a task or run. Under the Orkestra adapter every gate
in a task — lint, types, tests — carries the same ``task_id``, so the first
green gate was written into the others as "What later worked", a false sentence
that is hashed into ``content_hash`` and cannot be corrected, because the
signature is marked resolved at the same moment and the genuine fix is then
refused.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from provalume.schemas.memories import MemoryFilter, MemoryType

if TYPE_CHECKING:
    from provalume.sdk.client import Provalume

LINT = "ruff check src"
PARALLEL = "pytest -n auto tests/integration"
SERIAL = "pytest -p no:xdist tests/integration"


def _all_gotchas(pv: Provalume) -> list:
    return pv.memory_records(
        MemoryFilter(
            project_id=pv.project_id,
            memory_types=(MemoryType.GOTCHA,),
            include_terminal=True,
            current_only=False,
            limit=20,
        )
    )


def _gotchas(pv: Provalume) -> dict[str, dict]:
    """Every gotcha, keyed by the command that produced it."""
    return {
        str(m.content["command"]): {"content": m.content, "text": m.text} for m in _all_gotchas(pv)
    }


def test_an_unrelated_success_does_not_claim_credit_for_a_failure(pv: Provalume) -> None:
    """Two gates, one task. The passing one did not fix the other."""
    pv.record_verification(
        command=LINT,
        passed=False,
        excerpt="E501 line too long",
        error_kind="lint_error",
        task_id="task-1",
    )
    pv.record_verification(
        command=PARALLEL,
        passed=False,
        excerpt="E TimeoutError: deadlock in db fixture teardown",
        error_kind="test_failure",
        task_id="task-1",
    )
    pv.record_verification(command=SERIAL, passed=True, task_id="task-1")

    lint = _gotchas(pv)[LINT]
    assert lint["content"]["resolution"] is None, (
        f"an unrelated success was recorded as the fix: {lint['content']['resolution']}"
    )
    assert "What later worked" not in lint["text"], (
        f"memory holds a falsified sentence: {lint['text']!r}"
    )

    warning = pv.preflight(
        command=LINT, error_kind="lint_error", error_text="E501 line too long", record=False
    )
    assert "resolved" not in warning.summary.lower().splitlines()[0], (
        "an open failure is announced as resolved"
    )
    assert SERIAL not in warning.summary


def test_a_false_attribution_does_not_lock_out_the_genuine_fix(pv: Provalume) -> None:
    """The signature must stay open, or the real fix can never attach."""
    pv.record_verification(
        command=LINT,
        passed=False,
        excerpt="E501 line too long",
        error_kind="lint_error",
        task_id="task-1",
    )
    pv.record_verification(command=SERIAL, passed=True, task_id="task-1")

    # The lint gate is fixed and re-run, later in the same task.
    pv.record_verification(command=LINT, passed=True, task_id="task-1")

    resolution = _gotchas(pv)[LINT]["content"]["resolution"]
    assert resolution is not None, "the genuine fix could not attach"
    assert resolution["command"] == LINT


def test_the_same_command_passing_resolves_its_own_failure(pv: Provalume) -> None:
    pv.record_verification(
        command=PARALLEL,
        passed=False,
        excerpt="E TimeoutError: x",
        error_kind="test_failure",
        task_id="task-1",
    )
    pv.record_verification(command=PARALLEL, passed=True, task_id="task-1")

    resolution = _gotchas(pv)[PARALLEL]["content"]["resolution"]
    assert resolution is not None
    assert resolution["command"] == PARALLEL


def test_a_different_command_with_the_same_purpose_resolves(pv: Provalume) -> None:
    """The case the loose match existed to serve, now supported by evidence."""
    pv.record_verification(
        command=PARALLEL,
        passed=False,
        excerpt="E TimeoutError: x",
        error_kind="test_failure",
        purpose="the integration suite",
        task_id="task-1",
    )
    pv.record_verification(
        command=SERIAL, passed=True, purpose="the integration suite", task_id="task-1"
    )

    resolution = _gotchas(pv)[PARALLEL]["content"]["resolution"]
    assert resolution is not None, "a declared shared purpose did not link the fix"
    assert resolution["command"] == SERIAL


def test_one_success_may_resolve_two_failures_of_the_same_command(pv: Provalume) -> None:
    """One command, two distinct errors, two signatures — one fix for both."""
    pv.record_verification(
        command=PARALLEL,
        passed=False,
        excerpt="E TimeoutError: x",
        error_kind="test_failure",
        task_id="task-1",
    )
    pv.record_verification(
        command=PARALLEL,
        passed=False,
        excerpt="E ConnectionError: upstream reset",
        error_kind="test_failure",
        task_id="task-1",
    )
    pv.record_verification(command=PARALLEL, passed=True, task_id="task-1")

    found = _all_gotchas(pv)
    assert len(found) == 2, f"expected two signatures, got {len(found)}"
    assert all(m.content["resolution"] is not None for m in found)
