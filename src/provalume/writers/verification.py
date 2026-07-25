"""Verification and command events into procedural and episodic memory.

A passing verification produces a **procedural candidate**: the exact command,
keyed so that promotion later requires the *same* command to have passed
(ADR-0007). Promoting a runbook on some other command's evidence would be a
fabricated procedure, so the command string is content, not decoration.
"""

from __future__ import annotations

from typing import Any

from provalume.interchange.hashing import hash_content, hash_text
from provalume.policy.invalidation import subject_key
from provalume.schemas.events import Event
from provalume.schemas.memories import Memory, MemoryType
from provalume.schemas.scope import Scope, ScopeLevel
from provalume.schemas.trust import TrustState, VerificationState
from provalume.writers.failures import normalize_command


def derive_memory_id(event_id: str, kind: str) -> str:
    """Stable identifier derived from the originating event.

    Deterministic so a rebuild reproduces identifiers rather than minting new
    ones; prefixed by kind so one event can project into several records.
    """
    return hash_text(f"{kind}:{event_id}").removeprefix("sha256:")[:26].upper()


def with_content_hash(memory: Memory) -> Memory:
    return memory.model_copy(
        update={"content_hash": hash_content(memory.content, memory.text)}
    )


def scope_for(event: Event, *, level: ScopeLevel | None = None) -> Scope:
    """Build a record scope from an event.

    Defaults to branch scope when the event names a branch, repository scope
    otherwise. Narrow by default is the containment posture: widening is a
    promotion with its own evidence (ADR-0005), so a record that starts broad
    has skipped a check.
    """
    resolved = level or (ScopeLevel.BRANCH if event.branch else ScopeLevel.REPOSITORY)
    return Scope(
        level=resolved,
        project_id=event.project_id,
        repository_id=event.repository_id,
        branch=event.branch,
        run_id=event.run_id,
        task_id=event.task_id,
        attempt_id=event.attempt_id,
        agent_profile=event.agent_profile,
    )


def procedural_text(*, command: str, purpose: str, branch: str | None) -> str:
    parts = [f"`{command.strip()}` succeeded"]
    if purpose:
        parts.append(f"for {purpose}")
    if branch:
        parts.append(f"on branch {branch}")
    return " ".join(parts) + "."


def build_procedural(event: Event, *, landing_state: TrustState) -> Memory | None:
    """Project a passing verification into a procedural candidate.

    Returns ``None`` when the event carries no command — there is nothing to
    make a runbook out of, and inventing one from surrounding metadata would be
    the LLM-extraction behaviour this design refuses.
    """
    command = str(event.payload.get("command", "")).strip()
    if not command:
        return None

    purpose = str(event.payload.get("purpose", ""))
    content: dict[str, Any] = {
        "command": normalize_command(command),
        "raw_command": command,
        "purpose": purpose,
        "exit_code": event.payload.get("exit_code", 0),
        "duration_s": event.payload.get("duration_s"),
        "working_dir": event.payload.get("working_dir"),
    }
    memory = Memory(
        memory_id=derive_memory_id(event.event_id, "procedural"),
        memory_type=MemoryType.PROCEDURAL,
        content=content,
        text=procedural_text(command=command, purpose=purpose, branch=event.branch),
        scope=scope_for(event),
        run_id=event.run_id,
        task_id=event.task_id,
        attempt_id=event.attempt_id,
        source_event_ids=(event.event_id,),
        source=event.source,
        author_agent=event.agent_profile,
        adapter=event.adapter,
        model=event.model,
        effort=event.effort,
        commit_sha=event.commit_sha,
        valid_at=event.recorded_at,
        recorded_at=event.recorded_at,
        trust_state=landing_state,
        verification_state=VerificationState.PASSED,
        subject_key=subject_key(f"{command} {purpose}"),
    )
    return with_content_hash(memory)


def episodic_text(event: Event) -> str:
    """One-line summary of what happened.

    Deterministic construction: no locale-dependent formatting, no dict ordering.
    This text is hashed into ``content_hash``.
    """
    verb = {
        "verification.passed": "verification passed",
        "verification.failed": "verification FAILED",
        "command.succeeded": "command succeeded",
        "command.failed": "command FAILED",
        "attempt.completed": "attempt completed",
        "task.completed": "task completed",
        "run.completed": "run completed",
        "review.approved": "review approved",
        "review.rejected": "review REJECTED",
        "review.changes_requested": "review requested changes",
        "integration.landed": "integration landed",
        "integration.reverted": "integration reverted",
    }.get(event.event_type.value, event.event_type.value)

    bits = [verb]
    command = str(event.payload.get("command", "")).strip()
    if command:
        bits.append(f"`{command}`")
    if event.agent_profile:
        bits.append(f"by {event.agent_profile}")
    if event.branch:
        bits.append(f"on {event.branch}")
    if event.commit_sha:
        bits.append(f"at {event.commit_sha[:12]}")
    return " ".join(bits) + "."


def build_episodic(event: Event, *, landing_state: TrustState) -> Memory:
    """Project any lifecycle event into an episodic record.

    Episodic records never reach ``integrated``: the episode happened regardless
    of what landed, so the concept does not apply (ADR-0004).
    """
    verification = VerificationState.UNKNOWN
    if event.event_type.value in {"verification.passed", "command.succeeded"}:
        verification = VerificationState.PASSED
    elif event.event_type.value in {"verification.failed", "command.failed"}:
        verification = VerificationState.FAILED

    content: dict[str, Any] = {
        "event_type": event.event_type.value,
        "command": event.payload.get("command"),
        "exit_code": event.payload.get("exit_code"),
        "outcome": event.payload.get("outcome"),
        "agent_profile": event.agent_profile,
        "adapter": event.adapter,
        "model": event.model,
        "effort": event.effort,
    }
    memory = Memory(
        memory_id=derive_memory_id(event.event_id, "episodic"),
        memory_type=MemoryType.EPISODIC,
        content=content,
        text=episodic_text(event),
        scope=scope_for(event),
        run_id=event.run_id,
        task_id=event.task_id,
        attempt_id=event.attempt_id,
        source_event_ids=(event.event_id,),
        source=event.source,
        author_agent=event.agent_profile,
        adapter=event.adapter,
        model=event.model,
        effort=event.effort,
        commit_sha=event.commit_sha,
        valid_at=event.recorded_at,
        recorded_at=event.recorded_at,
        trust_state=landing_state,
        verification_state=verification,
        subject_key=subject_key(episodic_text(event)),
    )
    return with_content_hash(memory)


def build_semantic(event: Event, *, landing_state: TrustState) -> Memory | None:
    """Project an observed or changed repository fact into semantic memory.

    Semantic memory is the strictest category: it is the only one asserting what
    the project currently *is*, so it needs landed history before being served as
    current truth (ADR-0004). Below that it is still stored and retrievable — just
    labelled as branch-local or unconfirmed.
    """
    statement = str(event.payload.get("statement", "")).strip()
    if not statement:
        return None

    content: dict[str, Any] = {
        "statement": statement,
        "subject": event.payload.get("subject", ""),
        "category": event.payload.get("category", ""),
        "evidence": event.payload.get("evidence", ""),
        "replaces": event.payload.get("replaces"),
    }
    subject = str(event.payload.get("subject", "")) or statement
    memory = Memory(
        memory_id=derive_memory_id(event.event_id, "semantic"),
        memory_type=MemoryType.SEMANTIC,
        content=content,
        text=statement,
        scope=scope_for(event),
        run_id=event.run_id,
        task_id=event.task_id,
        attempt_id=event.attempt_id,
        source_event_ids=(event.event_id,),
        source=event.source,
        author_agent=event.agent_profile,
        adapter=event.adapter,
        model=event.model,
        effort=event.effort,
        commit_sha=event.commit_sha,
        valid_at=event.recorded_at,
        recorded_at=event.recorded_at,
        trust_state=landing_state,
        subject_key=subject_key(subject),
    )
    return with_content_hash(memory)
