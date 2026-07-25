"""What a resolved gotcha actually tells the reader.

Found by dogfooding round 2: after a real fix, the warning read "What later
worked: `<the exact command that failed>`" — true, and useless. The command was
never what changed. The datum that answers "what worked" was already recorded in
`resolution.commit_sha`, and was surfaced nowhere: not in the gotcha text, not in
`PreflightMatch`, not in the rendered warning.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from provalume.schemas.memories import Memory, MemoryFilter, MemoryType

if TYPE_CHECKING:
    from provalume.sdk.client import Provalume

COMMAND = "pytest -q tests/"
ALTERNATIVE = "pytest -p no:xdist tests/"
FAILURE = "E ConnectionError: transient upstream reset"
FIX_COMMIT = "b57ba729485c" + "0" * 28


def _gotcha(pv: Provalume) -> Memory:
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
    return found[0]


def _warn(pv: Provalume):
    return pv.preflight(command=COMMAND, error_kind="test_failure",
                        error_text=FAILURE, record=False)


def test_a_same_command_resolution_names_the_commit_not_the_command(pv: Provalume) -> None:
    pv.record_verification(command=COMMAND, passed=False, excerpt=FAILURE,
                           error_kind="test_failure", task_id="task-A")
    signature = _gotcha(pv).content["failure_signature"]
    pv.record_verification(command=COMMAND, passed=True, task_id="task-B",
                           commit_sha=FIX_COMMIT, resolves_signature=signature)

    text = _gotcha(pv).text
    assert f"after commit {FIX_COMMIT[:12]}" in text, (
        f"the resolving commit is still not surfaced: {text!r}"
    )
    assert f"What later worked: `{COMMAND}`" not in text, (
        f"the tautology survives in stored memory: {text!r}"
    )

    match = _warn(pv).matches[0]
    assert match.resolution_commit_sha == FIX_COMMIT
    assert match.resolved_at
    assert f"after commit {FIX_COMMIT[:12]}" in match.what_later_worked
    assert match.what_later_worked.strip() != COMMAND


def test_the_warning_renders_the_commit_on_its_own_line(pv: Provalume) -> None:
    pv.record_verification(command=COMMAND, passed=False, excerpt=FAILURE,
                           error_kind="test_failure", task_id="task-A")
    signature = _gotcha(pv).content["failure_signature"]
    pv.record_verification(command=COMMAND, passed=True, task_id="task-B",
                           commit_sha=FIX_COMMIT, resolves_signature=signature)

    summary = _warn(pv).summary
    line = next(ln for ln in summary.splitlines() if "What later worked" in ln)
    assert FIX_COMMIT[:12] in line, f"the warning line reads {line!r}"
    assert "resolved" in summary.splitlines()[0].lower()


def test_a_genuinely_different_command_is_still_named(pv: Provalume) -> None:
    """Where the fix *was* a different command, that command is the answer."""
    pv.record_verification(command=COMMAND, passed=False, excerpt=FAILURE,
                           error_kind="test_failure", purpose="the suite",
                           task_id="task-A")
    pv.record_verification(command=ALTERNATIVE, passed=True, purpose="the suite",
                           commit_sha=FIX_COMMIT, task_id="task-A")

    assert f"`{ALTERNATIVE}`" in _gotcha(pv).text
    match = _warn(pv).matches[0]
    assert match.what_later_worked.startswith(ALTERNATIVE)
    assert FIX_COMMIT[:12] in match.what_later_worked


def test_a_resolution_without_a_commit_still_anchors_in_time(pv: Provalume) -> None:
    """Git is not always available; the record must still say something useful."""
    pv.record_verification(command=COMMAND, passed=False, excerpt=FAILURE,
                           error_kind="test_failure", task_id="task-A")
    signature = _gotcha(pv).content["failure_signature"]
    pv.record_verification(command=COMMAND, passed=True, task_id="task-B",
                           resolves_signature=signature)

    match = _warn(pv).matches[0]
    assert match.what_later_worked.startswith("the same command passed")
    assert match.resolved_at and match.resolved_at in match.what_later_worked
