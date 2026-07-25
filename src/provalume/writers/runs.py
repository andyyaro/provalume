"""Run outcomes into performance memory.

Performance memory answers "which agent profile actually succeeds at which kind
of task?" — deterministically aggregated from outcome events, never from an
agent's self-assessment.

The aggregate is keyed on ``(agent_profile, adapter, model, task_category)``
rather than on an event, because a statistic is about a *series*. That makes it
the one memory type whose identity is not derived from a single event, and the
one for which ``integrated`` is meaningless: a statistic does not land in a
commit (ADR-0004).
"""

from __future__ import annotations

from typing import Any

from provalume import _time
from provalume.interchange.hashing import hash_text
from provalume.policy.invalidation import subject_key
from provalume.schemas.events import Event
from provalume.schemas.memories import Memory, MemoryType
from provalume.schemas.scope import Scope, ScopeLevel
from provalume.schemas.trust import TrustState
from provalume.writers.verification import with_content_hash


def performance_key(
    *,
    project_id: str,
    agent_profile: str,
    adapter: str,
    model: str,
    task_category: str,
) -> str:
    """Stable identity for one performance aggregate."""
    material = f"performance:{project_id}:{agent_profile}:{adapter}:{model}:{task_category}"
    return hash_text(material).removeprefix("sha256:")[:26].upper()


def performance_text(
    *,
    agent_profile: str,
    task_category: str,
    attempts: int,
    successes: int,
    approvals: int,
    verifications: int,
    fallbacks: int,
) -> str:
    """Render a performance aggregate.

    Rates are stated with their denominators. "80% success" over five attempts
    and over five hundred are different claims, and a reader choosing an agent
    needs to see which one this is.

    A bucket holding only verifications or approvals states those instead of
    claiming there were no attempts. Outcome events carry no task category, so
    they aggregate apart from the attempts they belong to, and rendering that
    bucket as "no recorded attempts" published the flat contradiction of the
    agent's own record: one `verified` memory saying five of five succeeded and
    another saying there was nothing to succeed at.
    """
    subject = f"{agent_profile} on {task_category}" if task_category else agent_profile
    counted = []
    if verifications:
        counted.append(f"{verifications} passed verification")
    if approvals:
        counted.append(f"{approvals} approved in review")
    if fallbacks:
        counted.append(f"{fallbacks} required fallback")

    if attempts == 0:
        if not counted:
            return f"{agent_profile}: no recorded attempts at {task_category or 'anything'}."
        return f"{subject}: " + ", ".join(counted) + ", and no attempts recorded."
    rate = successes / attempts
    parts = [f"{subject}: {successes}/{attempts} succeeded ({rate:.0%})", *counted]
    return ", ".join(parts) + "."


def build_performance(
    *,
    project_id: str,
    repository_id: str | None,
    agent_profile: str,
    adapter: str,
    model: str,
    effort: str,
    task_category: str,
    attempts: int,
    successes: int,
    approvals: int,
    verifications: int,
    fallbacks: int,
    first_seen_at: str,
    last_seen_at: str,
    source_event_ids: tuple[str, ...],
    landing_state: TrustState,
) -> Memory:
    """Build or rebuild one performance aggregate."""
    content: dict[str, Any] = {
        "agent_profile": agent_profile,
        "adapter": adapter,
        "model": model,
        "effort": effort,
        "task_category": task_category,
        "attempts": attempts,
        "successes": successes,
        "approvals": approvals,
        "verifications": verifications,
        "fallbacks": fallbacks,
        # `None`, not 0.0, when there is nothing to divide by: a rate of zero is
        # a claim that the agent failed everything, and a consumer ranking agents
        # by `success_rate` would read it as the worst possible record rather
        # than as the absence of one.
        "success_rate": round(successes / attempts, 4) if attempts else None,
        "first_seen_at": first_seen_at,
        "last_seen_at": last_seen_at,
    }
    memory = Memory(
        memory_id=performance_key(
            project_id=project_id,
            agent_profile=agent_profile,
            adapter=adapter,
            model=model,
            task_category=task_category,
        ),
        memory_type=MemoryType.PERFORMANCE,
        content=content,
        text=performance_text(
            agent_profile=agent_profile,
            task_category=task_category,
            attempts=attempts,
            successes=successes,
            approvals=approvals,
            verifications=verifications,
            fallbacks=fallbacks,
        ),
        # Repository-scoped: capability is a property of the agent and the
        # codebase, not of whichever branch the attempts happened on.
        scope=Scope(
            level=ScopeLevel.REPOSITORY,
            project_id=project_id,
            repository_id=repository_id,
        ),
        source_event_ids=source_event_ids,
        source=_dominant_source(source_event_ids),
        author_agent=agent_profile,
        adapter=adapter,
        model=model,
        effort=effort,
        valid_at=first_seen_at,
        recorded_at=last_seen_at,
        trust_state=landing_state,
        subject_key=subject_key(f"{agent_profile} {task_category} performance"),
    )
    return with_content_hash(memory)


def merge_performance(stored: Memory, fresh: Memory) -> Memory | None:
    """Fold a freshly accumulated bucket into the aggregate already on disk.

    The live write path projects one event at a time, so ``fresh`` holds one
    event's counts. Upserting it as-is — which is what a plain write does —
    replaces "9 of 10 succeeded" with "1 of 1 succeeded", and the stored
    aggregate then permanently disagrees with a rebuild from the same journal.
    A statistic is about a series, so it has to accumulate across writes.

    Returns ``None`` when there is nothing new to fold. Every event that
    contributed is named in ``source_event_ids``, so re-projecting an event that
    is already counted cannot inflate the numbers. Callers only ever offer a
    bucket that is wholly new or wholly already counted — ``apply`` projects a
    single event, ``catch_up`` starts past the watermark, and ``rebuild`` drops
    the projections first — which is what makes that check exact.
    """
    already = set(stored.source_event_ids)
    if set(fresh.source_event_ids) <= already:
        return None

    prior = stored.content
    latest = fresh.content

    def total(field: str) -> int:
        return int(prior.get(field, 0) or 0) + int(latest.get(field, 0) or 0)

    return build_performance(
        project_id=fresh.scope.project_id,
        repository_id=fresh.scope.repository_id,
        agent_profile=str(latest.get("agent_profile", "")),
        adapter=str(latest.get("adapter", "")),
        model=str(latest.get("model", "")),
        effort=str(latest.get("effort") or prior.get("effort") or ""),
        task_category=str(latest.get("task_category", "")),
        attempts=total("attempts"),
        successes=total("successes"),
        approvals=total("approvals"),
        verifications=total("verifications"),
        fallbacks=total("fallbacks"),
        first_seen_at=min(
            str(prior.get("first_seen_at") or latest["first_seen_at"]),
            str(latest["first_seen_at"]),
        ),
        last_seen_at=max(
            str(prior.get("last_seen_at") or latest["last_seen_at"]),
            str(latest["last_seen_at"]),
        ),
        source_event_ids=tuple(sorted(already | set(fresh.source_event_ids))),
        landing_state=fresh.trust_state,
    )


def _dominant_source(source_event_ids: tuple[str, ...]) -> Any:
    """Performance aggregates are derived by Provalume itself from kernel events.

    Marked ``kernel`` because the aggregation is deterministic arithmetic over
    trusted outcome events, not an agent's claim about its own record — which is
    exactly the thing that must never be self-reported.
    """
    from provalume.schemas.trust import Source

    return Source.KERNEL


class PerformanceAccumulator:
    """Accumulates outcome events into per-agent aggregates.

    Kept separate from the projector because it is stateful across events while
    every other writer is a pure per-event function. Order-independent: the same
    events in any order produce the same aggregate, which is what lets a rebuild
    reproduce it.
    """

    def __init__(self, *, project_id: str, repository_id: str | None = None) -> None:
        self.project_id = project_id
        self.repository_id = repository_id
        self._buckets: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    def observe(self, event: Event) -> None:
        agent = (event.agent_profile or "").strip()
        if not agent:
            return
        # Empty, not "general", when the event names no category. A
        # `verification.passed` carries none, and defaulting it to a category
        # name filed those outcomes under a task category the agent was never
        # asked to work on, next to the real bucket for the same agent.
        category = str(
            event.payload.get("task_category", event.payload.get("kind", ""))
        ).strip()
        key = (agent, event.adapter or "", event.model or "", category)
        bucket = self._buckets.setdefault(
            key,
            {
                "attempts": 0,
                "successes": 0,
                "approvals": 0,
                "verifications": 0,
                "fallbacks": 0,
                "effort": event.effort or "",
                "first_seen_at": event.recorded_at,
                "last_seen_at": event.recorded_at,
                "event_ids": [],
            },
        )
        bucket["event_ids"].append(event.event_id)
        bucket["first_seen_at"] = min(bucket["first_seen_at"], event.recorded_at)
        bucket["last_seen_at"] = max(bucket["last_seen_at"], event.recorded_at)

        kind = event.event_type.value
        if kind in {"attempt.completed", "task.completed"}:
            bucket["attempts"] += 1
            outcome = str(event.payload.get("outcome", "")).lower()
            if outcome in {"ok", "success", "succeeded", "passed", "accepted"}:
                bucket["successes"] += 1
            if event.payload.get("fallback"):
                bucket["fallbacks"] += 1
        elif kind == "verification.passed":
            bucket["verifications"] += 1
        elif kind == "review.approved":
            bucket["approvals"] += 1

    def build(self, *, landing_state: TrustState) -> list[Memory]:
        """Materialise the aggregates.

        Sorted by key so output order is deterministic regardless of dict
        insertion order — required for byte-identical rebuilds.

        A bucket is materialised whenever it counted anything. Requiring an
        attempt or a verification dropped reviewer agents entirely — a profile
        that only ever approves other agents' work produces approvals and nothing
        else, so review reliability was aggregated for no one.
        """
        out: list[Memory] = []
        for (agent, adapter, model, category) in sorted(self._buckets):
            bucket = self._buckets[(agent, adapter, model, category)]
            if not (bucket["attempts"] or bucket["verifications"] or bucket["approvals"]):
                continue
            out.append(
                build_performance(
                    project_id=self.project_id,
                    repository_id=self.repository_id,
                    agent_profile=agent,
                    adapter=adapter,
                    model=model,
                    effort=str(bucket["effort"]),
                    task_category=category,
                    attempts=int(bucket["attempts"]),
                    successes=int(bucket["successes"]),
                    approvals=int(bucket["approvals"]),
                    verifications=int(bucket["verifications"]),
                    fallbacks=int(bucket["fallbacks"]),
                    first_seen_at=str(bucket["first_seen_at"]),
                    last_seen_at=str(bucket["last_seen_at"]),
                    source_event_ids=tuple(sorted(set(bucket["event_ids"]))),
                    landing_state=landing_state,
                )
            )
        return out


def run_summary_text(event: Event) -> str:
    """Text for a completed run's episodic record."""
    outcome = str(event.payload.get("outcome", "completed"))
    tasks = event.payload.get("task_count")
    bits = [f"Run {event.run_id or '(unknown)'} {outcome}"]
    if tasks is not None:
        bits.append(f"across {tasks} task(s)")
    if event.branch:
        bits.append(f"on {event.branch}")
    return " ".join(bits) + f" at {_time.normalize(event.recorded_at)}."
