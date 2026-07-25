"""Orkestra adapter — the reference integration.

**This module imports nothing from Orkestra.** It accepts plain dictionaries
shaped like Orkestra's records and translates them into Provalume events. That
keeps the dependency pointing one way (ADR-0014), makes the adapter testable with
fixtures alone, and means Orkestra can take Provalume as an optional dependency
rather than the reverse.

``tests/security/test_integration_boundary.py`` asserts the absence.

Two rules that shape everything here:

**Structured records, never prose scraping.** An adapter that parsed agent output
would be doing extraction, which is interpretation of possibly-hostile text, which
is the poisoning primitive this design refuses (ADR-0007).

**Memory never overrides policy.** The preflight gate returns a warning an
orchestrator may surface. It cannot block a dispatch, change a retry budget, or
veto an assignment. A memory-poisoning bug must not become an
orchestration-control bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from provalume.schemas.events import Event, EventType
from provalume.schemas.retrieval import Digest, PreflightResult
from provalume.schemas.trust import Source

if TYPE_CHECKING:
    from provalume.sdk.client import Provalume

#: Orkestra's attempt error taxonomy, mapped to Provalume error kinds. Kept
#: explicit rather than passed through, so an unrecognised kind is visible as
#: "unknown" instead of silently becoming its own signature bucket.
ERROR_KIND_MAP: dict[str, str] = {
    "TIMEOUT": "timeout",
    "VERIFY_FAILED": "test_failure",
    "POLICY_VIOLATION": "policy_violation",
    "ADAPTER_ERROR": "adapter_error",
    "MERGE_CONFLICT": "merge_conflict",
    "QUOTA_EXHAUSTED": "quota",
    "CANCELLED": "cancelled",
}


@dataclass
class OrkestraContext:
    """Scope fields shared by a batch of records from one run."""

    project_id: str
    repository_id: str | None = None
    run_id: str | None = None
    branch: str | None = None
    base_commit: str | None = None

    def fields(self, **overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "repository_id": self.repository_id,
            "run_id": self.run_id,
            "branch": self.branch,
            "base_commit": self.base_commit,
        }
        base.update({k: v for k, v in overrides.items() if v is not None})
        return {k: v for k, v in base.items() if v is not None}


class OrkestraAdapter:
    """Translates Orkestra records into Provalume events.

    Every method returns the recorded :class:`Event` so the caller can link
    outcomes back — the preflight gate in particular depends on being able to
    associate a warning with what happened next.
    """

    def __init__(self, provalume: Provalume, context: OrkestraContext) -> None:
        self.pv = provalume
        self.context = context

    # -- runs and tasks ----------------------------------------------------

    def run_started(self, *, run_id: str, **extra: Any) -> Event:
        return self.pv.record_event(
            EventType.RUN_STARTED,
            source=Source.ADAPTER,
            payload={"orkestra": True, **extra},
            **self.context.fields(run_id=run_id),
        )

    def run_completed(self, *, run_id: str, outcome: str, task_count: int = 0,
                      **extra: Any) -> Event:
        return self.pv.record_event(
            EventType.RUN_COMPLETED,
            source=Source.ADAPTER,
            payload={"outcome": outcome, "task_count": task_count, **extra},
            **self.context.fields(run_id=run_id),
        )

    def task_completed(
        self,
        *,
        task_id: str,
        outcome: str,
        kind: str = "general",
        agent: str | None = None,
        adapter: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        **extra: Any,
    ) -> Event:
        return self.pv.record_event(
            EventType.TASK_COMPLETED,
            source=Source.ADAPTER,
            payload={"outcome": outcome, "task_category": kind, **extra},
            **self.context.fields(
                task_id=task_id, agent_profile=agent, adapter=adapter,
                model=model, effort=effort,
            ),
        )

    def attempt_completed(
        self,
        *,
        task_id: str,
        attempt_id: str,
        outcome: str,
        error_kind: str | None = None,
        agent: str | None = None,
        adapter: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        fallback: bool = False,
        worktree: str | None = None,
        **extra: Any,
    ) -> Event:
        """Record an attempt result.

        Feeds performance memory: which agent profile actually succeeds at which
        task category, aggregated deterministically rather than self-reported.
        """
        return self.pv.record_event(
            EventType.ATTEMPT_COMPLETED,
            source=Source.ADAPTER,
            payload={
                "outcome": outcome,
                "error_kind": ERROR_KIND_MAP.get(error_kind or "", error_kind or ""),
                "task_category": extra.pop("kind", "general"),
                "fallback": fallback,
                **extra,
            },
            **self.context.fields(
                task_id=task_id, attempt_id=attempt_id, agent_profile=agent,
                adapter=adapter, model=model, effort=effort, worktree=worktree,
            ),
        )

    # -- verification ------------------------------------------------------

    def verification(
        self,
        *,
        command: str,
        passed: bool,
        exit_code: int | None = None,
        excerpt: str = "",
        task_id: str | None = None,
        attempt_id: str | None = None,
        agent: str | None = None,
        worktree: str | None = None,
        purpose: str = "",
        **extra: Any,
    ) -> Event:
        """Record a verification result — the evidence everything else needs.

        This is the single most valuable thing an orchestrator can supply. A
        failure becomes a gotcha keyed on a deterministic signature; a success
        becomes a procedural candidate keyed on the exact command.
        """
        return self.pv.record_verification(
            command=command,
            passed=passed,
            exit_code=exit_code,
            excerpt=excerpt,
            purpose=purpose,
            error_kind=extra.pop("error_kind", "verify_failed" if not passed else ""),
            **self.context.fields(
                task_id=task_id, attempt_id=attempt_id,
                agent_profile=agent, worktree=worktree,
            ),
        )

    # -- review ------------------------------------------------------------

    def review_verdict(
        self,
        *,
        reviewer: str,
        approved: bool,
        changes_requested: bool = False,
        subject: str = "",
        finding: str = "",
        findings: tuple[str, ...] = (),
        task_id: str | None = None,
        attempt_id: str | None = None,
        **extra: Any,
    ) -> Event:
        """Record a review verdict.

        Orkestra keeps verdicts inside ``attempts.result`` JSON rather than in a
        dedicated table (verified by reading its schema), so this is an explicit
        call at the point the verdict is produced rather than a table read.

        ``reviewer`` matters: it is compared against the record's author, and a
        self-review never promotes.
        """
        return self.pv.record_review(
            reviewer=reviewer,
            approved=approved,
            changes_requested=changes_requested,
            subject=subject,
            finding=finding,
            findings=findings,
            **self.context.fields(
                task_id=task_id, attempt_id=attempt_id, agent_profile=reviewer, **extra
            ),
        )

    def reviewer_finding(
        self,
        *,
        finding: str,
        reviewer: str,
        severity: str = "",
        files: tuple[str, ...] = (),
        task_id: str | None = None,
        attempt_id: str | None = None,
    ) -> Event:
        return self.pv.record_event(
            EventType.REVIEW_FINDING,
            source=Source.ADAPTER,
            payload={
                "finding": finding,
                "reviewer": reviewer,
                "severity": severity,
                "files": list(files),
            },
            **self.context.fields(
                task_id=task_id, attempt_id=attempt_id, agent_profile=reviewer
            ),
        )

    # -- human decisions ---------------------------------------------------

    def human_decision(
        self,
        *,
        question: str,
        selected: str,
        rejected: tuple[str, ...] = (),
        rationale: str = "",
        authority: str = "",
        task_id: str | None = None,
        **extra: Any,
    ) -> Event:
        """Record a resolved human decision gate.

        ``rejected`` is the reusable part: without it, nothing stops an agent
        re-proposing what a human already turned down.
        """
        return self.pv.record_decision(
            question=question,
            selected=selected,
            rejected=rejected,
            rationale=rationale,
            authority=authority,
            **self.context.fields(task_id=task_id, **extra),
        )

    # -- git ---------------------------------------------------------------

    def integration_landed(
        self,
        *,
        commit_sha: str,
        target: str = "run",
        task_id: str | None = None,
        branch: str | None = None,
    ) -> Event:
        """Record that work landed. What semantic truth requires."""
        # `branch` goes through fields() rather than alongside it: fields()
        # already carries the context branch, so passing both would collide.
        fields = self.context.fields(task_id=task_id, branch=branch)
        return self.pv.record_integration(
            commit_sha=commit_sha,
            target=target,
            **fields,
        )

    def integration_reverted(self, *, commit_sha: str, branch: str | None = None) -> Event:
        return self.pv.record_event(
            EventType.INTEGRATION_REVERTED,
            source=Source.ADAPTER,
            payload={"branch": branch or self.context.branch or ""},
            commit_sha=commit_sha,
            **self.context.fields(),
        )

    def branch_rejected(self, *, branch: str, reason: str = "") -> Event:
        """Record that a branch was abandoned.

        Everything recorded on it becomes permanently non-truth while remaining
        available as negative experience.
        """
        return self.pv.record_event(
            EventType.BRANCH_REJECTED,
            source=Source.HUMAN,
            payload={"branch": branch, "reason": reason},
            **self.context.fields(branch=branch),
        )

    # -- reading -----------------------------------------------------------

    def brief_digest(
        self,
        *,
        query: str,
        char_budget: int = 2_000,
        task_id: str | None = None,
        agent: str | None = None,
    ) -> Digest:
        """Compose a digest for splicing into a task brief.

        Intended for the orchestrator's single prompt choke point, so one call
        reaches every adapter with no per-vendor code.
        """
        return self.pv.recall(
            query,
            branch=self.context.branch,
            task_id=task_id,
            agent_profile=agent,
            limit=20,
        ).digest(char_budget=char_budget)

    def preflight(
        self,
        *,
        command: str = "",
        subsystem: str = "",
        files: tuple[str, ...] = (),
    ) -> PreflightResult:
        """Check before dispatch or retry.

        Returns a warning. **It cannot block**: Orkestra's policy engine remains
        the only authority, and a memory system that could override policy would
        be one an attacker could use to override policy.
        """
        return self.pv.preflight(command=command, subsystem=subsystem, files=files)


# --- Failure semantics ------------------------------------------------------


def safe_digest(
    adapter: OrkestraAdapter,
    *,
    query: str,
    char_budget: int = 2_000,
    **kwargs: Any,
) -> Digest | None:
    """Compose a digest, failing **open** on error.

    Memory is an enhancement. A memory outage must not stop a run, so this
    swallows failures and returns ``None`` for the caller to treat as "no context
    available" (ADR-0014).

    Provenance corruption is the opposite case and is handled inside retrieval:
    a record whose provenance cannot be resolved is dropped rather than served,
    because serving it would break the core claim.
    """
    try:
        return adapter.brief_digest(query=query, char_budget=char_budget, **kwargs)
    except Exception:
        # Deliberate: a memory outage is not a run outage.
        return None


def safe_preflight(
    adapter: OrkestraAdapter, **kwargs: Any
) -> PreflightResult | None:
    """Run the preflight gate, failing open on error."""
    try:
        return adapter.preflight(**kwargs)
    except Exception:
        # See safe_digest: fail open.
        return None


def is_available() -> bool:
    """Whether Provalume can be used here.

    Orkestra calls this to decide whether to wire memory in at all. Provalume is
    optional: without it installed, or with a broken database, Orkestra behaves
    exactly as it did before.
    """
    try:
        import provalume  # noqa: F401
    except ImportError:
        return False
    return True
