"""Human decisions into decision memory.

Decisions are the one category whose authority is a person rather than a test. A
human decision *is* project truth because a human made it, so it reaches
``integrated`` on authority (ADR-0004) — but only when ``source=human``.
Agent-*proposed* decision records start ``quarantined`` like anything else.

The rejected alternatives are the reusable part. A decision record that says only
what was chosen cannot stop an agent from re-proposing what was already rejected,
which is the failure this category exists to prevent.
"""

from __future__ import annotations

from typing import Any

from provalume.policy.invalidation import subject_key
from provalume.schemas.events import Event
from provalume.schemas.memories import Memory, MemoryType
from provalume.schemas.scope import ScopeLevel
from provalume.schemas.trust import Source, TrustState
from provalume.writers.verification import derive_memory_id, scope_for, with_content_hash


def decision_text(
    *,
    selected: str,
    rejected: tuple[str, ...],
    rationale: str,
    authority: str,
) -> str:
    parts = [f"Decision: {selected}."]
    if rejected:
        parts.append(f"Rejected: {', '.join(rejected)}.")
    if rationale:
        parts.append(f"Rationale: {rationale}")
    if authority:
        parts.append(f"(decided by {authority})")
    return " ".join(parts)


def build_decision(event: Event, *, landing_state: TrustState) -> Memory | None:
    """Project a human decision event into decision memory."""
    selected = str(event.payload.get("selected", event.payload.get("option", ""))).strip()
    if not selected:
        return None

    raw_rejected = event.payload.get("rejected", event.payload.get("alternatives", []))
    rejected: tuple[str, ...] = ()
    if isinstance(raw_rejected, (list, tuple)):
        rejected = tuple(str(r).strip() for r in raw_rejected if str(r).strip())
    elif isinstance(raw_rejected, str) and raw_rejected.strip():
        rejected = (raw_rejected.strip(),)

    rationale = str(event.payload.get("rationale", event.payload.get("note", ""))).strip()
    authority = str(event.payload.get("authority", event.payload.get("decided_by", ""))).strip()
    consequences = str(event.payload.get("consequences", "")).strip()
    question = str(event.payload.get("question", event.payload.get("subject", ""))).strip()

    content: dict[str, Any] = {
        "question": question,
        "selected": selected,
        "rejected": list(rejected),
        "rationale": rationale,
        "authority": authority,
        "consequences": consequences,
    }

    # A human decision applies to the repository, not to the branch it happened
    # to be made on. Scoping it to a branch would lose it the moment that branch
    # merged, which is the opposite of what a decision record is for.
    level = ScopeLevel.REPOSITORY if event.source is Source.HUMAN else None

    memory = Memory(
        memory_id=derive_memory_id(event.event_id, "decision"),
        memory_type=MemoryType.DECISION,
        content=content,
        text=decision_text(
            selected=selected,
            rejected=rejected,
            rationale=rationale,
            authority=authority,
        )[:8192],
        scope=scope_for(event, level=level),
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
        subject_key=subject_key(question or selected),
    )
    return with_content_hash(memory)


def rejected_alternatives(memory: Memory) -> tuple[str, ...]:
    """Alternatives this decision rejected.

    Used by the preflight gate: proposing something a decision already rejected
    should produce a warning, not a rediscovery.
    """
    raw = memory.content.get("rejected", [])
    if isinstance(raw, (list, tuple)):
        return tuple(str(r) for r in raw if str(r).strip())
    return ()
