"""Review verdicts into lesson memory.

A rejection is a **lesson**, not a blemish. It records what a reviewer objected
to, so a later agent proposing the same thing can be warned. A later approval of
the same subject attaches as resolution evidence, turning "this was rejected" into
"this was rejected, and here is what satisfied the reviewer".

Rejections are stored as gotchas rather than as their own category because the
consumer is identical: the preflight gate asking "has this been tried?". A
reviewer's objection and a failing test are both prior evidence that an approach
did not work.
"""

from __future__ import annotations

from typing import Any

from provalume.policy.invalidation import subject_key
from provalume.schemas.events import Event
from provalume.schemas.memories import Memory, MemoryType
from provalume.schemas.trust import ReviewState, TrustState, VerificationState
from provalume.writers.verification import derive_memory_id, scope_for, with_content_hash


def lesson_text(*, reviewer: str, finding: str, subject: str, branch: str | None) -> str:
    parts = []
    if subject:
        parts.append(f"{subject}:")
    parts.append("rejected in review")
    if reviewer:
        parts.append(f"by {reviewer}")
    if branch:
        parts.append(f"on {branch}")
    head = " ".join(parts) + "."
    return f"{head} {finding}".strip() if finding else head


def build_lesson(event: Event, *, landing_state: TrustState) -> Memory | None:
    """Project a review rejection into a lesson.

    ``verification_state`` stays ``UNKNOWN``: a reviewer's objection is not a
    command result, and claiming otherwise would let review evidence stand in for
    verification evidence in the promotion rules.
    """
    reviewer = str(event.payload.get("reviewer", event.agent_profile or "")).strip()
    finding = str(event.payload.get("finding", event.payload.get("reason", ""))).strip()
    subject = str(event.payload.get("subject", "")).strip()

    if not (finding or subject):
        # A bare rejection with no stated reason teaches nothing; the episodic
        # record still captures that it happened.
        return None

    content: dict[str, Any] = {
        "reviewer": reviewer,
        "finding": finding,
        "subject": subject,
        "verdict": "rejected",
        "files": event.payload.get("files", []),
        "resolution": None,
    }
    memory = Memory(
        memory_id=derive_memory_id(event.event_id, "lesson"),
        memory_type=MemoryType.GOTCHA,
        content=content,
        text=lesson_text(
            reviewer=reviewer, finding=finding, subject=subject, branch=event.branch
        ),
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
        verification_state=VerificationState.UNKNOWN,
        review_state=ReviewState.REJECTED,
        subject_key=subject_key(f"{subject} {finding}"),
    )
    return with_content_hash(memory)


def build_finding(event: Event, *, landing_state: TrustState) -> Memory | None:
    """Project a standalone reviewer finding into a lesson.

    Distinct from a rejection: a finding can accompany an approval. Repeated
    findings across reviews are what eval scenario 18 measures.
    """
    finding = str(event.payload.get("finding", "")).strip()
    if not finding:
        return None

    reviewer = str(event.payload.get("reviewer", event.agent_profile or "")).strip()
    severity = str(event.payload.get("severity", "")).strip()
    content: dict[str, Any] = {
        "reviewer": reviewer,
        "finding": finding,
        "severity": severity,
        "files": event.payload.get("files", []),
        "verdict": "finding",
        "resolution": None,
    }
    prefix = f"[{severity}] " if severity else ""
    text = f"{prefix}Reviewer finding: {finding}"
    if reviewer:
        text += f" (raised by {reviewer})"

    memory = Memory(
        memory_id=derive_memory_id(event.event_id, "finding"),
        memory_type=MemoryType.GOTCHA,
        content=content,
        text=text,
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
        subject_key=subject_key(finding),
    )
    return with_content_hash(memory)


def attach_approval(lesson: Memory, approval: Event) -> Memory:
    """Record that a later review approved the subject of an earlier rejection.

    The lesson keeps ``review_state=REJECTED`` — the rejection happened and the
    record of it is the point. What changes is the resolution, which is what a
    future reader needs: not just "this was objected to" but "and this is what
    satisfied the objection".
    """
    content = dict(lesson.content)
    content["resolution"] = {
        "event_id": approval.event_id,
        "reviewer": approval.payload.get("reviewer", approval.agent_profile),
        "recorded_at": approval.recorded_at,
        "commit_sha": approval.commit_sha,
        "note": approval.payload.get("note", ""),
    }
    text = lesson.text
    if "Later approved" not in text:
        reviewer = content["resolution"]["reviewer"] or "a reviewer"
        text = f"{text} Later approved by {reviewer}."

    return with_content_hash(
        lesson.model_copy(
            update={
                "content": content,
                "text": text[:8192],
                "source_event_ids": tuple(
                    dict.fromkeys([*lesson.source_event_ids, approval.event_id])
                ),
            }
        )
    )


def is_independent(*, reviewer: str | None, author: str | None) -> bool:
    """Whether a reviewer is distinct from the record's author.

    Compared case-insensitively on the trimmed profile name. When either side is
    unknown the answer is ``True`` — an unknown reviewer is not evidence of a
    self-review, and treating it as one would block legitimate promotion whenever
    an integration omits an optional field. The promotion rule still requires an
    approval event to exist at all.
    """
    if not reviewer or not author:
        return True
    return reviewer.strip().lower() != author.strip().lower()
