"""How an orchestrator wires Provalume in.

A sketch, not a runnable script: it shows the call sites without requiring
Orkestra to be installed. The real integration lives in a draft PR against
Orkestra; see docs/integration/ORKESTRA.md.

Three things this demonstrates:

  1. Provalume is optional — `is_available()` gates everything.
  2. Retrieval fails open; a memory outage is not a run outage.
  3. Generated context files never reach a commit.
"""

from __future__ import annotations

from typing import Any

from provalume.integrations.generic import assert_clean, materialized, splice_digest
from provalume.integrations.orkestra import (
    OrkestraAdapter,
    OrkestraContext,
    is_available,
    safe_digest,
    safe_preflight,
)


def build_memory(config: Any, run: Any, workspace: Any) -> OrkestraAdapter | None:
    """Optional by construction: without Provalume, this returns None and the
    orchestrator behaves exactly as it did before."""
    if not is_available():
        return None

    from provalume import Provalume

    return OrkestraAdapter(
        Provalume.open(project_id=config.project_name),
        OrkestraContext(
            project_id=config.project_name,
            repository_id=workspace.remote_url,
            run_id=run.run_id,
            branch=workspace.branch,
            base_commit=workspace.base_commit,
        ),
    )


def render_brief(memory: OrkestraAdapter | None, task: Any, instructions: str) -> str:
    """The single prompt choke point. One splice reaches every adapter."""
    if memory is None:
        return instructions

    # Fails open: None means "no context available", not "stop the run".
    digest = safe_digest(memory, query=task.spec.title, char_budget=2000,
                         task_id=task.task_id)
    if digest is None:
        return instructions

    # Appended *after* the instructions: putting retrieved memory first would
    # give it the position of primary instruction.
    return splice_digest(instructions, digest)


def before_dispatch(memory: OrkestraAdapter | None, task: Any, fix_context: str) -> str:
    """Warn before repeating a known-failed action.

    Memory never overrides policy: this appends a warning the orchestrator may
    surface. It cannot block dispatch or change a retry budget.
    """
    if memory is None:
        return fix_context

    command = task.spec.acceptance[0] if task.spec.acceptance else ""
    warning = safe_preflight(memory, command=command, subsystem=task.spec.kind)
    if warning is None or not warning.matched:
        return fix_context
    return f"{fix_context}\n\n{warning.summary}"


async def run_task(memory: OrkestraAdapter | None, task: Any, workspace: Any,
                   brief: Any, adapter: Any, workspace_manager: Any) -> Any:
    """Run one task, recording evidence and cleaning up generated files."""
    if memory is None:
        return await adapter.run(brief)

    digest = safe_digest(memory, query=task.spec.title, task_id=task.task_id)

    if digest is not None:
        # The context manager guarantees cleanup even if the task raises, so a
        # crash cannot leave a file for `git add -A` to sweep into the commit.
        with materialized(digest, workspace.path):
            result = await adapter.run(brief)
    else:
        result = await adapter.run(brief)

    # Belt to the context manager's braces, before anything stages.
    assert_clean(workspace.path)

    memory.attempt_completed(
        task_id=task.task_id,
        attempt_id=result.attempt_id,
        outcome=result.status,
        error_kind=result.error_kind,
        agent=result.agent_name,
        adapter=adapter.adapter_id,
        kind=task.spec.kind,
    )
    return result


def after_verification(memory: OrkestraAdapter | None, task: Any, outcome: Any) -> None:
    """Record the verification result — the evidence everything else needs."""
    if memory is None:
        return
    for command in task.spec.acceptance or []:
        memory.verification(
            command=command,
            passed=outcome.passed,
            exit_code=outcome.exit_code,
            excerpt=outcome.summary[:8000],
            task_id=task.task_id,
        )


def after_review(memory: OrkestraAdapter | None, task: Any, verdict: Any) -> None:
    """Record the verdict. Orkestra keeps these inside attempts.result JSON, so
    this is an explicit call rather than a table read."""
    if memory is None:
        return
    memory.review_verdict(
        reviewer=verdict.reviewer,
        approved=verdict.approved,
        changes_requested=verdict.changes_requested,
        subject=task.spec.title,
        finding=verdict.summary,
        task_id=task.task_id,
        attempt_id=verdict.attempt_id,
    )


def after_integration(memory: OrkestraAdapter | None, task: Any, commit_sha: str) -> None:
    """Record that work landed. What semantic truth requires."""
    if memory is None:
        return
    memory.integration_landed(commit_sha=commit_sha, target="run",
                              task_id=task.task_id)
