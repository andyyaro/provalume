"""Deterministic projection of events into memory records.

This is where ADR-0007's event→memory mappings actually run. The projector is a
pure function of the journal: replaying the same events produces byte-identical
memories, including their identifiers, which is what makes ``provalume rebuild``
meaningful rather than merely available.

Two consequences of that determinism worth stating, because they constrain how
this file may be edited:

* **No wall-clock time.** Every timestamp comes from an event. A projection that
  read ``now()`` would produce different output on every rebuild.
* **No dict-order dependence.** Iteration is over sorted keys, and accumulated
  state is materialised in sorted order.

Promotion is applied here too, but never invented: each candidate is offered to
:mod:`provalume.policy.promotion`, which decides, and every decision — including
refusals — is recorded as a transition.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from provalume.policy import invalidation, promotion
from provalume.schemas.events import Event, EventType
from provalume.schemas.freshness import FreshnessState, RelevanceVerdict, ReverificationOutcome
from provalume.schemas.memories import CLAIM_TYPES, Memory, MemoryType, Transition
from provalume.schemas.trust import (
    TERMINAL_STATES,
    IntegrationState,
    ReviewState,
    Source,
    TrustState,
    rank,
)
from provalume.store.journal import Journal
from provalume.store.repository import MemoryRepository
from provalume.writers import decisions, failures, reviews, runs, verification

#: Events that produce an episodic record. Deliberately not "everything": an
#: `mcp.call` audit entry or a `warning.shown` feedback event is journal
#: material, not something an agent should read back as history.
_EPISODIC_EVENTS = frozenset(
    {
        EventType.VERIFICATION_PASSED,
        EventType.VERIFICATION_FAILED,
        EventType.COMMAND_SUCCEEDED,
        EventType.COMMAND_FAILED,
        EventType.ATTEMPT_COMPLETED,
        EventType.TASK_COMPLETED,
        EventType.RUN_COMPLETED,
        EventType.REVIEW_APPROVED,
        EventType.REVIEW_REJECTED,
        EventType.REVIEW_CHANGES_REQUESTED,
        EventType.INTEGRATION_LANDED,
        EventType.INTEGRATION_REVERTED,
    }
)


#: Sources permitted to withdraw an existing memory — to invalidate it, or to
#: reject it along with its branch. Withdrawal is the mirror image of promotion
#: and needs the same structural gate: `rejected` is terminal, with no
#: revalidation path (:data:`provalume.policy.promotion.REFUSE_REJECTED`), so an
#: ungated handler lets a two-line JSONL file permanently withdraw the
#: recipient's own verified memories. An import arrives ``source=import``
#: precisely so a file cannot promote itself (threat T17); nothing about that
#: reasoning is specific to the upward direction.
_WITHDRAWAL_SOURCES = frozenset({Source.HUMAN, Source.KERNEL})


@dataclass
class ProjectionStats:
    """What a projection run did. Returned so callers can report honestly."""

    events_processed: int = 0
    memories_written: int = 0
    memories_updated: int = 0
    promotions: int = 0
    refusals: int = 0
    invalidations: int = 0
    supersessions: int = 0
    rejections: int = 0
    contradictions: int = 0
    signatures: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "events_processed": self.events_processed,
            "memories_written": self.memories_written,
            "memories_updated": self.memories_updated,
            "promotions": self.promotions,
            "refusals": self.refusals,
            "invalidations": self.invalidations,
            "supersessions": self.supersessions,
            "rejections": self.rejections,
            "contradictions": self.contradictions,
            "signatures": self.signatures,
            "notes": list(self.notes),
        }


class Projector:
    """Applies events to memory projections."""

    def __init__(
        self,
        journal: Journal,
        repository: MemoryRepository,
        *,
        poisoning_threshold: float = 0.5,
    ) -> None:
        self.journal = journal
        self.repository = repository
        self.poisoning_threshold = poisoning_threshold

    # -- entry points ------------------------------------------------------

    def apply(self, event: Event) -> ProjectionStats:
        """Project one event. Used on the live write path.

        Advances the projection watermark, so a database written entirely through
        the live path does not look permanently behind its own journal to
        ``audit``.
        """
        stats = ProjectionStats()
        accumulators: dict[str, runs.PerformanceAccumulator] = {}
        self._apply_one(event, stats, accumulators=accumulators)
        self._flush_accumulators(accumulators, stats)
        stats.events_processed = 1
        if event.seq is not None and event.seq > self.repository.projection_seq():
            self.repository.set_projection_seq(event.seq)
        return stats

    def rebuild(self, *, project_id: str | None = None) -> ProjectionStats:
        """Drop and rebuild every projection from the journal.

        The correctness claim of ADR-0002 in executable form: if this cannot
        reproduce the projections, the journal is not really the source of truth.
        """
        self.repository.delete_all_projections(project_id=project_id)
        stats = ProjectionStats()
        accumulators: dict[str, runs.PerformanceAccumulator] = {}
        last_seq = 0

        for event in self.journal.iter_all(project_id=project_id):
            self._apply_one(event, stats, accumulators=accumulators)
            stats.events_processed += 1
            last_seq = max(last_seq, event.seq or 0)

        self._flush_accumulators(accumulators, stats)
        self.repository.set_projection_seq(last_seq, rebuilt=True)
        return stats

    def catch_up(self, *, project_id: str | None = None) -> ProjectionStats:
        """Project events recorded since the last projection point."""
        stats = ProjectionStats()
        accumulators: dict[str, runs.PerformanceAccumulator] = {}
        since = self.repository.projection_seq()
        last_seq = since

        for event in self.journal.iter_all(project_id=project_id):
            if (event.seq or 0) <= since:
                continue
            self._apply_one(event, stats, accumulators=accumulators)
            stats.events_processed += 1
            last_seq = max(last_seq, event.seq or 0)

        self._flush_accumulators(accumulators, stats)
        if last_seq > since:
            self.repository.set_projection_seq(last_seq)
        return stats

    # -- per-event ---------------------------------------------------------

    def _apply_one(
        self,
        event: Event,
        stats: ProjectionStats,
        *,
        accumulators: dict[str, runs.PerformanceAccumulator],
    ) -> None:
        landing = self._landing_state(event)

        handler = {
            EventType.VERIFICATION_FAILED: self._on_failure,
            EventType.COMMAND_FAILED: self._on_failure,
            EventType.VERIFICATION_PASSED: self._on_success,
            EventType.COMMAND_SUCCEEDED: self._on_success,
            EventType.REVIEW_REJECTED: self._on_review_rejected,
            EventType.REVIEW_CHANGES_REQUESTED: self._on_review_rejected,
            EventType.REVIEW_APPROVED: self._on_review_approved,
            EventType.REVIEW_FINDING: self._on_review_finding,
            EventType.HUMAN_DECISION: self._on_decision,
            EventType.FACT_OBSERVED: self._on_fact,
            EventType.FACT_CHANGED: self._on_fact_changed,
            EventType.INTEGRATION_LANDED: self._on_integration,
            EventType.INTEGRATION_REVERTED: self._on_revert,
            EventType.BRANCH_REJECTED: self._on_branch_rejected,
            EventType.HUMAN_INVALIDATION: self._on_human_invalidation,
            EventType.HUMAN_REJECTION: self._on_human_rejection,
            EventType.AGENT_PROPOSAL: self._on_proposal,
            EventType.AGENT_FAILURE_REPORT: self._on_failure,
            EventType.AGENT_OBSERVATION: self._on_fact,
            EventType.BLAST_RADIUS_RECORDED: self._on_blast_radius,
            EventType.FRESHNESS_TRIGGERED: self._on_freshness_trigger,
            EventType.RELEVANCE_ASSESSED: self._on_relevance,
            EventType.REVERIFICATION_EXECUTED: self._on_reverification,
        }.get(event.event_type)

        if handler is not None:
            handler(event, landing, stats)

        if event.event_type in _EPISODIC_EVENTS:
            episode = verification.build_episodic(event, landing_state=landing)
            self._write(episode, stats)
            # An episodic record is a deterministic projection of the very event
            # that produced it, so that event is its own evidence. It climbs to
            # `verified` and stops: `integrated` is meaningless for a record of
            # something that happened (ADR-0004), and the ladder refuses it.
            if event.is_evidence:
                self._try_promote(episode, (event,), stats)

        if event.event_type in {
            EventType.ATTEMPT_COMPLETED,
            EventType.TASK_COMPLETED,
            EventType.VERIFICATION_PASSED,
            EventType.REVIEW_APPROVED,
        }:
            key = f"{event.project_id}::{event.repository_id or ''}"
            acc = accumulators.get(key)
            if acc is None:
                acc = runs.PerformanceAccumulator(
                    project_id=event.project_id, repository_id=event.repository_id
                )
                accumulators[key] = acc
            acc.observe(event)

    def _landing_state(self, event: Event) -> TrustState:
        """Where a memory derived from this event starts.

        Mirrors :func:`provalume.policy.admission.landing_state`, applied at
        projection time so that a rebuild reaches the same landing state as the
        original write. Reading it off the stored event's integrity metadata
        rather than rescanning keeps the two paths from drifting.
        """
        risk = float(event.integrity.get("poisoning", {}).get("risk", 0.0))
        if risk >= self.poisoning_threshold:
            return TrustState.QUARANTINED
        if event.source is Source.AGENT or event.event_type.value.startswith("agent."):
            return TrustState.QUARANTINED
        if event.source is Source.IMPORT:
            return TrustState.QUARANTINED
        return TrustState.OBSERVED

    def _poisoning(self, event: Event) -> tuple[float, tuple[str, ...]]:
        info = event.integrity.get("poisoning", {})
        return float(info.get("risk", 0.0)), tuple(info.get("matches", []))

    # -- freshness (ADR-0020) ----------------------------------------------
    #
    # Every freshness handler is gated on `source == kernel`: an imported or
    # agent-sourced freshness event is stored append-only, like every event,
    # and derives nothing — an imported `freshness.triggered` must not be
    # able to relabel a local record any more than an imported claim can
    # raise its trust (threats T17, T28). None of these handlers can touch
    # trust: they set the freshness column and nothing else.

    def _freshness_target(self, event: Event) -> Memory | None:
        if event.source is not Source.KERNEL:
            return None
        record_id = str(event.payload.get("record_id", ""))
        if not record_id:
            return None
        memory = self.repository.get(record_id)
        if memory is None or memory.scope.project_id != event.project_id:
            # A record from another project must be unreachable even by a
            # crafted record_id (threat T9).
            return None
        if memory.trust_state in TERMINAL_STATES:
            # A withdrawn record has nothing left to be suspect about.
            return None
        return memory

    def _set_freshness(self, memory: Memory, state: FreshnessState, stats: ProjectionStats) -> None:
        if memory.freshness is state:
            return
        self._write(memory.model_copy(update={"freshness": state}), stats, update=True)

    def _on_blast_radius(self, event: Event, landing: TrustState, stats: ProjectionStats) -> None:
        memory = self._freshness_target(event)
        if memory is None:
            return
        paths = tuple(str(p) for p in event.payload.get("paths", []) if p)
        if not paths:
            return
        # One transaction: the radius rows, the discharge, and the column move
        # together or not at all (nested tx reuses the outer transaction).
        with self.repository.db.tx():
            self.repository.replace_blast_radius(
                record_id=memory.memory_id,
                project_id=memory.scope.project_id,
                method=str(event.payload.get("method", "")),
                paths=paths,
            )
            # A radius is a fresh measurement against the tree that contains
            # every landed commit, so it discharges whatever was outstanding.
            self.repository.clear_triggers(
                project_id=memory.scope.project_id, record_id=memory.memory_id
            )
            self._set_freshness(memory, FreshnessState.CURRENT, stats)

    def _on_freshness_trigger(
        self, event: Event, landing: TrustState, stats: ProjectionStats
    ) -> None:
        memory = self._freshness_target(event)
        if memory is None:
            return
        trigger_commit = str(event.payload.get("trigger_commit", ""))
        if not trigger_commit:
            return  # malformed: stored, derives nothing
        with self.repository.db.tx():
            self.repository.add_outstanding_trigger(
                project_id=memory.scope.project_id,
                record_id=memory.memory_id,
                trigger_commit=trigger_commit,
            )
            if memory.freshness is not FreshnessState.STALE:
                # Stale dominates: a further touch cannot make a failed re-run
                # less failed.
                self._set_freshness(memory, FreshnessState.SUSPECT, stats)

    def _on_relevance(self, event: Event, landing: TrustState, stats: ProjectionStats) -> None:
        """A verdict answers ONE trigger. The record returns to `current`
        only when no trigger remains outstanding — a single irrelevant
        verdict must never discharge changes it was not asked about (a false
        `current` is the failure this axis exists to prevent)."""
        memory = self._freshness_target(event)
        if memory is None:
            return
        verdict = str(event.payload.get("verdict", ""))
        trigger_commit = str(event.payload.get("trigger_commit", ""))
        if verdict == RelevanceVerdict.IRRELEVANT.value and trigger_commit:
            with self.repository.db.tx():
                self.repository.discharge_trigger(
                    project_id=memory.scope.project_id,
                    record_id=memory.memory_id,
                    trigger_commit=trigger_commit,
                )
                outstanding = self.repository.outstanding_triggers(
                    project_id=memory.scope.project_id, record_id=memory.memory_id
                )
                if not outstanding and memory.freshness is FreshnessState.SUSPECT:
                    self._set_freshness(memory, FreshnessState.CURRENT, stats)
        elif (
            verdict == RelevanceVerdict.RELEVANT.value
            and memory.freshness is not FreshnessState.STALE
        ):
            self._set_freshness(memory, FreshnessState.SUSPECT, stats)
        # Any other verdict is a malformed event: stored, derives nothing.

    def _on_reverification(self, event: Event, landing: TrustState, stats: ProjectionStats) -> None:
        memory = self._freshness_target(event)
        if memory is None:
            return
        outcome = str(event.payload.get("outcome", ""))
        if outcome == ReverificationOutcome.PASSED.value:
            # The re-run executed against the tree containing every landed
            # commit, so it answers every outstanding trigger at once.
            with self.repository.db.tx():
                self.repository.clear_triggers(
                    project_id=memory.scope.project_id, record_id=memory.memory_id
                )
                self._set_freshness(memory, FreshnessState.CURRENT, stats)
        elif outcome == ReverificationOutcome.FAILED.value:
            self._set_freshness(memory, FreshnessState.STALE, stats)
        # `errored` is the engine failing, not the record failing: no
        # transition at all (fail-open, I5).

    # -- handlers ----------------------------------------------------------

    def _on_failure(self, event: Event, landing: TrustState, stats: ProjectionStats) -> None:
        risk, matches = self._poisoning(event)
        gotcha, signature = failures.build_gotcha(
            event, landing_state=landing, poisoning_risk=risk, poisoning_matches=matches
        )

        # Fold into an existing gotcha with the same signature rather than
        # creating a near-duplicate; repetition is a count, not a new record.
        existing_rows = self.repository.signature_rows(event.project_id, signature.value)
        target = gotcha
        if existing_rows:
            prior = self.repository.get(str(existing_rows[0]["memory_id"]))
            if prior is not None:
                count = self.repository.record_signature(
                    signature=signature.value,
                    project_id=event.project_id,
                    memory_id=prior.memory_id,
                    command=signature.command,
                    error_kind=signature.error_kind,
                    when=event.recorded_at,
                )
                target = failures.merge_occurrence(prior, event, count)
                target = target.model_copy(
                    update={"text": failures.elevated_warning_text(target, count)[:8192]}
                )
                target = verification.with_content_hash(target)
                self._write(target, stats, update=True)
                stats.signatures += 1
                self._try_promote(target, (event,), stats)
                return

        self._write(target, stats)
        self.repository.record_signature(
            signature=signature.value,
            project_id=event.project_id,
            memory_id=target.memory_id,
            command=signature.command,
            error_kind=signature.error_kind,
            when=event.recorded_at,
        )
        stats.signatures += 1
        self._try_promote(target, (event,), stats)

    def _on_success(self, event: Event, landing: TrustState, stats: ProjectionStats) -> None:
        procedural = verification.build_procedural(event, landing_state=landing)
        if procedural is not None:
            self._write(procedural, stats)
            self._try_promote(procedural, (event,), stats)

        # A success may resolve a prior failure of the same command.
        command = failures.normalize_command(str(event.payload.get("command", "")))
        resolves = str(event.payload.get("resolves_signature", "")).strip()
        if resolves:
            self._link_resolution(event, resolves, stats)
        elif command:
            self._resolve_matching_failures(event, command, stats)

    def _resolve_matching_failures(
        self, event: Event, command: str, stats: ProjectionStats
    ) -> None:
        """Attach a success as the resolution of an unresolved failure.

        Inference needs a real link, not merely co-occurrence. Two count: the
        same command now passing, and a *different* command declaring the same
        ``purpose`` — a fix is often a different command that achieved the same
        end, which is why purpose is carried on the gotcha at all.

        Scope alone is not a link. Under an orchestrator every gate in a task —
        lint, types, tests — shares a ``task_id``, so a bare scope match wrote
        the first success into *every* open gotcha as "what later worked". That
        sentence is false, it is hashed into ``content_hash``, and it cannot be
        corrected later, because the signature is marked resolved at the same
        moment and the genuine fix is then refused.

        Several genuine matches are possible — one command can fail twice with
        different errors, and one success resolves both — so this does not stop
        at the first.
        """
        scope_id = event.task_id or event.run_id
        if not scope_id:
            return
        purpose = str(event.payload.get("purpose", "")).strip().lower()
        for row in self.repository.all_signatures(event.project_id):
            if row.get("resolved_by_id"):
                continue
            prior = self.repository.get(str(row["memory_id"]))
            if prior is None:
                continue
            if (prior.task_id or prior.run_id) != scope_id:
                continue
            if prior.content.get("resolution"):
                continue
            if not _resolves(prior, command=command, purpose=purpose):
                continue
            updated = failures.build_resolution_link(gotcha=prior, resolution_event=event)
            self._write(updated, stats, update=True)
            self.repository.set_signature_resolution(
                project_id=event.project_id,
                signature=str(row["signature"]),
                resolved_by_id=event.event_id,
            )
            fix_id = _procedural_id(event)
            if fix_id is not None:
                # From the fix to the failure it resolved. `resolves_gotcha_id` is
                # read off this link and is documented as being set on "this
                # record documents what finally worked", so the arrow has to leave
                # the record that documents the fix.
                self.repository.add_link(
                    project_id=event.project_id,
                    from_id=fix_id,
                    to_id=prior.memory_id,
                    link_type="resolved_by",
                )

    def _link_resolution(self, event: Event, signature: str, stats: ProjectionStats) -> None:
        """Attach a success that names the failure signature it resolves.

        The declared path reaches across runs, where inference cannot, and it
        needs the same two properties as the inferred one. A resolution already
        recorded is never overwritten: a second success naming the same signature
        would otherwise replace the real fix in ``content["resolution"]`` — which
        is what the preflight gate serves as ``what_later_worked`` — while the
        rendered text went on naming the first, and the signature row would be
        repointed at the wrong event. And the fix is linked to the failure, so
        the record that documents what worked says so.
        """
        for row in self.repository.signature_rows(event.project_id, signature):
            prior = self.repository.get(str(row["memory_id"]))
            if prior is None or prior.content.get("resolution"):
                continue
            updated = failures.build_resolution_link(gotcha=prior, resolution_event=event)
            self._write(updated, stats, update=True)
            self.repository.set_signature_resolution(
                project_id=event.project_id,
                signature=signature,
                resolved_by_id=event.event_id,
            )
            fix_id = _procedural_id(event)
            if fix_id is not None:
                self.repository.add_link(
                    project_id=event.project_id,
                    from_id=fix_id,
                    to_id=prior.memory_id,
                    link_type="resolved_by",
                )

    def _on_review_rejected(
        self, event: Event, landing: TrustState, stats: ProjectionStats
    ) -> None:
        lesson = reviews.build_lesson(event, landing_state=landing)
        if lesson is not None:
            self._write(lesson, stats)
            self._try_promote(lesson, (event,), stats)
        self._apply_review_state(event, ReviewState.REJECTED, stats)

    def _on_review_approved(
        self, event: Event, landing: TrustState, stats: ProjectionStats
    ) -> None:
        self._apply_review_state(event, ReviewState.APPROVED, stats)

        # A later approval of the same subject resolves an earlier lesson.
        subject = str(event.payload.get("subject", "")).strip()
        if subject:
            key = invalidation.subject_key(subject)
            for memory in self._lessons_naming_subject(event.project_id, key):
                if not memory.content.get("resolution"):
                    self._write(reviews.attach_approval(memory, event), stats, update=True)

    def _on_review_finding(self, event: Event, landing: TrustState, stats: ProjectionStats) -> None:
        finding = reviews.build_finding(event, landing_state=landing)
        if finding is not None:
            self._write(finding, stats)
            self._try_promote(finding, (event,), stats)

    def _on_decision(self, event: Event, landing: TrustState, stats: ProjectionStats) -> None:
        decision = decisions.build_decision(event, landing_state=landing)
        if decision is None:
            return
        self._write(decision, stats)
        # A human decision climbs the ladder on authority, one rung at a time so
        # every step is recorded (ADR-0005).
        if event.source is Source.HUMAN:
            self._promote_to(decision, TrustState.INTEGRATED, (event,), stats)
        else:
            self._try_promote(decision, (event,), stats)

    def _on_fact(self, event: Event, landing: TrustState, stats: ProjectionStats) -> None:
        semantic = verification.build_semantic(event, landing_state=landing)
        if semantic is None:
            return
        self._write(semantic, stats)
        self._detect_contradictions(semantic, stats)
        self._try_promote(semantic, (event,), stats)

    def _on_fact_changed(self, event: Event, landing: TrustState, stats: ProjectionStats) -> None:
        """A fact changed: supersede the old record, never overwrite it."""
        semantic = verification.build_semantic(event, landing_state=landing)
        if semantic is None:
            return

        replaced_id = str(event.payload.get("replaces", "")).strip()
        old: Memory | None = None
        if replaced_id:
            old = self.repository.get(replaced_id)
        else:
            for candidate in self._memories_with_subject(event.project_id, semantic.subject_key):
                if (
                    candidate.memory_type is MemoryType.SEMANTIC
                    and candidate.is_current
                    and candidate.content_hash != semantic.content_hash
                ):
                    old = candidate
                    break

        if old is not None:
            decision = invalidation.can_supersede(old, semantic)
            if decision.allowed:
                semantic = semantic.model_copy(update={"supersedes_id": old.memory_id})
                superseded = old.model_copy(
                    update={
                        "trust_state": TrustState.SUPERSEDED,
                        "invalid_at": event.recorded_at,
                    }
                )
                self._transition(
                    superseded,
                    old.trust_state,
                    TrustState.SUPERSEDED,
                    decision.rule,
                    (event.event_id,),
                    event,
                    note=f"superseded by {semantic.memory_id}",
                )
                stats.supersessions += 1
                self.repository.add_link(
                    project_id=event.project_id,
                    from_id=semantic.memory_id,
                    to_id=old.memory_id,
                    link_type="supersedes",
                )
            else:
                stats.notes.append(f"supersession refused for {old.memory_id}: {decision.reason}")

        self._write(semantic, stats)
        self._try_promote(semantic, (event,), stats)

    def _on_integration(self, event: Event, landing: TrustState, stats: ProjectionStats) -> None:
        """Work landed: records from that branch or task become promotable.

        A landing may also name the failure it resolves. That claim belongs here
        rather than on the verification that passed: a pass proves a command
        succeeded in some worktree, and an orchestrator discards worktrees for
        merge conflicts, rejected reviews, and exhausted budgets. Only a landing
        proves the repository changed, which is what "what later worked" asserts.
        """
        resolves = str(event.payload.get("resolves_signature", "")).strip()
        if resolves:
            self._link_resolution(event, resolves, stats)

        target = str(event.payload.get("target", "run"))
        state = (
            IntegrationState.ACCEPTED_USER if target == "user" else IntegrationState.INTEGRATED_RUN
        )
        for memory in self._memories_for_landing(event):
            # Promotion is branch-wide; re-anchoring is not. A landing makes
            # every claim on that branch promotable, but only the records of
            # *this* task were carried by *this* commit — and matching on task
            # or branch means each successive landing would otherwise re-stamp
            # them all, leaving every record wearing the last commit to land.
            # A record with no commit at all still takes one: it is on the
            # branch, and an unanchored record is worse than a coarse one.
            landed_here = bool(event.task_id) and memory.task_id == event.task_id
            update: dict[str, Any] = {
                "integration_state": state,
                "source_event_ids": tuple(
                    dict.fromkeys([*memory.source_event_ids, event.event_id])
                ),
            }
            if landed_here or not memory.commit_sha:
                # `commit_sha` is what currency is judged against ("still true
                # at this commit?"), and a record that has landed is true of
                # what landed — not of the base its worktree branched from,
                # which is only where the verification that observed it ran.
                # The observation stays recoverable through `source_event_ids`.
                update["commit_sha"] = event.commit_sha or memory.commit_sha
            updated = memory.model_copy(update=update)
            self._write(updated, stats, update=True)
            self._climb(updated, TrustState.INTEGRATED, event, stats)

    def _on_revert(self, event: Event, landing: TrustState, stats: ProjectionStats) -> None:
        for memory in self._memories_for_landing(event):
            reverted = memory.model_copy(
                update={
                    "integration_state": IntegrationState.REVERTED,
                    "invalid_at": event.recorded_at,
                }
            )
            decision = invalidation.can_invalidate(
                memory, rule=invalidation.RULE_INVALIDATE_REVERTED
            )
            if not decision.allowed:
                continue
            self._transition(
                reverted.model_copy(update={"trust_state": TrustState.INVALIDATED}),
                memory.trust_state,
                TrustState.INVALIDATED,
                decision.rule,
                (event.event_id,),
                event,
                note="integration reverted",
            )
            stats.invalidations += 1

    def _on_branch_rejected(
        self, event: Event, landing: TrustState, stats: ProjectionStats
    ) -> None:
        """A branch was abandoned: its records become permanently non-truth.

        They are kept, not deleted. The lesson of a rejected approach is the most
        reusable thing memory holds; what must never happen is that lesson being
        served as current fact (threat T5).
        """
        if not self._may_withdraw(event, stats):
            return
        # `or` rather than a dict default: callers routinely pass an explicit
        # empty branch, and `.get(k, default)` returns the empty string rather
        # than falling back, which silently matched nothing.
        branch = str(event.payload.get("branch") or event.branch or "").strip()
        if not branch:
            return
        for memory in self.repository.find(_branch_filter(event.project_id, branch)):
            decision = invalidation.can_reject(memory, rule=invalidation.RULE_REJECT_BRANCH)
            if not decision.allowed:
                continue
            rejected = memory.model_copy(
                update={
                    "trust_state": TrustState.REJECTED,
                    "invalid_at": event.recorded_at,
                }
            )
            self._transition(
                rejected,
                memory.trust_state,
                TrustState.REJECTED,
                decision.rule,
                (event.event_id,),
                event,
                note=f"branch {branch} rejected; retained as negative experience",
            )
            stats.rejections += 1

    def _on_human_invalidation(
        self, event: Event, landing: TrustState, stats: ProjectionStats
    ) -> None:
        memory = self._withdrawal_target(event, stats)
        if memory is None:
            return
        memory_id = memory.memory_id
        decision = invalidation.can_invalidate(memory, rule=invalidation.RULE_INVALIDATE_HUMAN)
        if not decision.allowed:
            stats.notes.append(f"invalidation refused for {memory_id}: {decision.reason}")
            return
        self._transition(
            memory.model_copy(
                update={
                    "trust_state": TrustState.INVALIDATED,
                    "invalid_at": event.recorded_at,
                }
            ),
            memory.trust_state,
            TrustState.INVALIDATED,
            decision.rule,
            (event.event_id,),
            event,
            note=str(event.payload.get("reason", "")),
        )
        stats.invalidations += 1

    def _on_human_rejection(
        self, event: Event, landing: TrustState, stats: ProjectionStats
    ) -> None:
        memory = self._withdrawal_target(event, stats)
        if memory is None:
            return
        decision = invalidation.can_reject(memory, rule=invalidation.RULE_REJECT_HUMAN)
        if not decision.allowed:
            return
        self._transition(
            memory.model_copy(
                update={
                    "trust_state": TrustState.REJECTED,
                    "invalid_at": event.recorded_at,
                }
            ),
            memory.trust_state,
            TrustState.REJECTED,
            decision.rule,
            (event.event_id,),
            event,
            note=str(event.payload.get("reason", "")),
        )
        stats.rejections += 1

    def _may_withdraw(self, event: Event, stats: ProjectionStats) -> bool:
        """Whether this event carries the authority to withdraw a memory.

        The refusal is recorded rather than silent: a shared export that tried
        to reject the recipient's records is exactly what an operator wants to
        hear about, and a handler that simply returned would leave no trace.
        """
        if event.source in _WITHDRAWAL_SOURCES:
            return True
        stats.notes.append(
            f"{event.event_type.value} from {event.source} refused: only human "
            "or kernel events may withdraw a memory"
        )
        return False

    def _withdrawal_target(self, event: Event, stats: ProjectionStats) -> Memory | None:
        """The memory a withdrawal event names, when it may act on it at all."""
        if not self._may_withdraw(event, stats):
            return None
        memory_id = str(event.payload.get("memory_id", "")).strip()
        memory = self.repository.get(memory_id) if memory_id else None
        if memory is None:
            return None
        # `get` resolves an identifier, not a project. `project_id` is the
        # isolation boundary (threat T9), so a database holding two projects —
        # reachable through a second `Provalume(db, project_id=...)` or an
        # `allow_foreign_project` import — must not let one withdraw the
        # other's records.
        if memory.scope.project_id != event.project_id:
            stats.notes.append(
                f"withdrawal refused for {memory_id}: it belongs to project "
                f"{memory.scope.project_id!r}, not {event.project_id!r}"
            )
            return None
        return memory

    def _on_proposal(self, event: Event, landing: TrustState, stats: ProjectionStats) -> None:
        """An agent proposed a memory. It lands quarantined, always.

        No evidence in the payload can change that: the trust state comes from
        the source, which is structural (threat T2).
        """
        raw_type = str(event.payload.get("memory_type", "semantic"))
        try:
            memory_type = MemoryType(raw_type)
        except ValueError:
            stats.notes.append(f"proposal {event.event_id} names unknown memory type {raw_type!r}")
            return

        text = str(event.payload.get("text", "")).strip()
        if not text:
            return

        risk, matches = self._poisoning(event)
        memory = Memory(
            memory_id=verification.derive_memory_id(event.event_id, "proposal"),
            memory_type=memory_type,
            content=dict(event.payload.get("content", {})) or {"text": text},
            text=text[:8192],
            scope=verification.scope_for(event),
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
            trust_state=TrustState.QUARANTINED,
            poisoning_risk=risk,
            poisoning_matches=matches,
            subject_key=invalidation.subject_key(text),
        )
        self._write(verification.with_content_hash(memory), stats)

    # -- promotion ---------------------------------------------------------

    def _try_promote(
        self, memory: Memory, evidence: tuple[Event, ...], stats: ProjectionStats
    ) -> None:
        """Advance a memory as far as its evidence allows, one rung at a time."""
        current = memory
        for _ in range(len(promotion.LADDER)):
            target = promotion.next_rung(current.trust_state)
            if target is None:
                return
            decision = promotion.can_promote(
                current,
                target,
                evidence=evidence,
                actor_source=Source.KERNEL,
                poisoning_threshold=self.poisoning_threshold,
            )
            if not decision.allowed:
                # Only record a refusal when evidence existed and policy said no.
                # An ordinary "nothing supports this yet" is not an event worth a
                # row; a *refused* promotion is.
                if decision.rule not in {
                    promotion.REFUSE_NO_EVIDENCE,
                    promotion.REFUSE_TYPE_CEILING,
                    promotion.REFUSE_NOT_LANDED,
                }:
                    self._record_refusal(current, target, decision, evidence, stats)
                return
            current = self._promote_once(current, target, decision, evidence, stats)

    def _promote_to(
        self,
        memory: Memory,
        target_state: TrustState,
        evidence: tuple[Event, ...],
        stats: ProjectionStats,
    ) -> None:
        """Climb to a specific state, one recorded rung at a time."""
        current = memory
        while rank(current.trust_state) < rank(target_state):
            target = promotion.next_rung(current.trust_state)
            if target is None:
                return
            decision = promotion.can_promote(
                current,
                target,
                evidence=evidence,
                actor_source=Source.KERNEL,
                poisoning_threshold=self.poisoning_threshold,
            )
            if not decision.allowed:
                self._record_refusal(current, target, decision, evidence, stats)
                return
            current = self._promote_once(current, target, decision, evidence, stats)

    def _climb(
        self,
        memory: Memory,
        target_state: TrustState,
        event: Event,
        stats: ProjectionStats,
    ) -> None:
        """Climb using the memory's own recorded evidence events."""
        evidence = tuple(self.journal.get_many(memory.source_event_ids))
        evidence = (*evidence, event)
        self._promote_to(memory, target_state, evidence, stats)

    def _promote_once(
        self,
        memory: Memory,
        target: TrustState,
        decision: promotion.Decision,
        evidence: tuple[Event, ...],
        stats: ProjectionStats,
    ) -> Memory:
        review, integration = promotion.evidence_states(evidence)
        updates: dict[str, Any] = {"trust_state": target}
        if review is not ReviewState.NONE:
            updates["review_state"] = review
        if integration is not IntegrationState.NONE:
            updates["integration_state"] = integration
        promoted = memory.model_copy(update=updates)

        self._transition(
            promoted,
            memory.trust_state,
            target,
            decision.rule,
            decision.evidence_event_ids,
            evidence[-1] if evidence else None,
            note=decision.reason,
        )
        stats.promotions += 1
        return promoted

    def _record_refusal(
        self,
        memory: Memory,
        target: TrustState,
        decision: promotion.Decision,
        evidence: tuple[Event, ...],
        stats: ProjectionStats,
    ) -> None:
        transition = Transition.create(
            memory_id=memory.memory_id,
            from_state=memory.trust_state,
            to_state=target,
            policy_rule=decision.rule,
            actor="provalume.projector",
            actor_source=Source.KERNEL,
            evidence_event_ids=tuple(e.event_id for e in evidence),
            scope=memory.scope.describe(),
            allowed=False,
            note=decision.reason,
        )
        self.repository.transition(memory, transition)
        stats.refusals += 1

    def _transition(
        self,
        memory: Memory,
        from_state: TrustState,
        to_state: TrustState,
        rule: str,
        evidence_ids: tuple[str, ...],
        event: Event | None,
        *,
        note: str = "",
    ) -> None:
        transition = Transition.create(
            memory_id=memory.memory_id,
            from_state=from_state,
            to_state=to_state,
            policy_rule=rule,
            actor="provalume.projector",
            actor_source=event.source if event else Source.KERNEL,
            evidence_event_ids=evidence_ids,
            scope=memory.scope.describe(),
            allowed=True,
            note=note[:2048],
        )
        self.repository.transition(memory, transition)

    # -- helpers -----------------------------------------------------------

    def _write(
        self, memory: Memory | None, stats: ProjectionStats, *, update: bool = False
    ) -> None:
        if memory is None:
            return
        self.repository.upsert(memory)
        if update:
            stats.memories_updated += 1
        else:
            stats.memories_written += 1

    def _apply_review_state(self, event: Event, state: ReviewState, stats: ProjectionStats) -> None:
        """Stamp a review verdict onto the records it actually concerns.

        Only claim types (semantic, procedural, decision) are stamped by attempt
        association. A record type — a gotcha, an episode, a statistic — is
        stamped only when the reviewer named its subject, because approving a fix
        is not approving the failure that prompted it.

        Every verdict is linked, not only the first one to change the state. An
        earlier self-approval already sets ``review_state`` to ``approved``, so
        skipping on that alone dropped the later *independent* approval from
        ``source_event_ids`` — and `_to_reviewed`, which reads its evidence from
        exactly that list, then refused the promotion as a self-review. An agent
        could cap its own work at ``verified`` by approving it first.
        """
        subject = str(event.payload.get("subject", "")).strip()
        subject_match = invalidation.subject_key(subject) if subject else ""

        for memory in self._memories_for_attempt(event):
            named_by_subject = _names_subject(memory, subject_match)
            if memory.memory_type not in CLAIM_TYPES and not named_by_subject:
                continue
            source_event_ids = tuple(dict.fromkeys([*memory.source_event_ids, event.event_id]))
            if memory.review_state is state and source_event_ids == memory.source_event_ids:
                continue
            self._write(
                memory.model_copy(
                    update={
                        "review_state": state,
                        "source_event_ids": source_event_ids,
                    }
                ),
                stats,
                update=True,
            )

    def _memories_for_attempt(self, event: Event) -> list[Memory]:
        """Records a review verdict concerns.

        Attempt scope is preferred over task scope: a verdict is about one
        attempt's work, and stamping it across every record of a multi-attempt
        task would credit or blame attempts the reviewer never saw.
        """
        from provalume.schemas.memories import MemoryFilter

        base: dict[str, Any] = {
            "project_id": event.project_id,
            "include_terminal": True,
            "current_only": False,
            "limit": 200,
        }
        if event.attempt_id:
            return self.repository.find(MemoryFilter(attempt_id=event.attempt_id, **base))
        if event.task_id:
            return self.repository.find(MemoryFilter(task_id=event.task_id, **base))

        # No attempt or task was named — the common case when Provalume is driven
        # directly rather than by an orchestrator. Fall back to the branch, and
        # only to claim types, so a verdict lands on assertions about the codebase
        # rather than on every episode recorded that day.
        if event.branch:
            return [
                m
                for m in self.repository.find(MemoryFilter(branch=event.branch, **base))
                if m.memory_type in CLAIM_TYPES
            ]
        return []

    def _memories_for_landing(self, event: Event) -> list[Memory]:
        """Records that a landed integration makes promotable.

        Restricted to claim types. What lands in a commit is an assertion about
        the codebase — a procedure, a fact, a decision. A gotcha's value does not
        depend on a merge, and marking one ``integrated`` would say the failure
        landed when what landed was the fix.
        """
        from provalume.schemas.memories import MemoryFilter

        spec = MemoryFilter(
            project_id=event.project_id,
            include_terminal=False,
            current_only=True,
            limit=500,
        )
        # `or` rather than a dict default: callers routinely pass an explicit
        # empty branch, and `.get(k, default)` returns the empty string rather
        # than falling back, which silently matched nothing.
        branch = str(event.payload.get("branch") or event.branch or "").strip()
        task_id = event.task_id
        out: list[Memory] = []
        for memory in self.repository.find(spec):
            if memory.memory_type not in CLAIM_TYPES:
                continue
            matches_task = bool(task_id) and memory.task_id == task_id
            matches_branch = bool(branch) and memory.scope.branch == branch
            if matches_task or matches_branch:
                out.append(memory)
        return out

    def _lessons_naming_subject(self, project_id: str, key: str) -> list[Memory]:
        """Lessons whose subject is the one a reviewer named.

        A lesson's ``subject_key`` covers the subject *and* the finding together,
        so it can never equal a key built from the subject alone — which is why
        an exact-key lookup attached a resolution to bare rejections only, and
        never to the ones that said what was wrong. The stated subject is
        compared instead, over the recent lessons plus whatever the exact key
        still matches, so an older bare rejection stays reachable.
        """
        from provalume.schemas.memories import MemoryFilter

        if not key:
            return []
        recent = self.repository.find(
            MemoryFilter(
                project_id=project_id,
                memory_types=(MemoryType.GOTCHA,),
                include_terminal=False,
                current_only=True,
                limit=200,
            )
        )
        out: list[Memory] = []
        seen: set[str] = set()
        for memory in [*recent, *self._memories_with_subject(project_id, key)]:
            if memory.memory_id in seen or memory.memory_type is not MemoryType.GOTCHA:
                continue
            if not _names_subject(memory, key):
                continue
            seen.add(memory.memory_id)
            out.append(memory)
        return out

    def _memories_with_subject(self, project_id: str, subject: str) -> list[Memory]:
        from provalume.schemas.memories import MemoryFilter

        if not subject:
            return []
        return self.repository.find(
            MemoryFilter(
                project_id=project_id,
                subject_key=subject,
                include_terminal=False,
                current_only=True,
                limit=50,
            )
        )

    def _detect_contradictions(self, memory: Memory, stats: ProjectionStats) -> None:
        for other in self._memories_with_subject(memory.scope.project_id, memory.subject_key):
            if invalidation.contradicts(memory, other):
                self.repository.add_contradiction(
                    project_id=memory.scope.project_id,
                    subject_key=memory.subject_key,
                    memory_id_a=memory.memory_id,
                    memory_id_b=other.memory_id,
                )
                stats.contradictions += 1

    def _flush_accumulators(
        self,
        accumulators: dict[str, runs.PerformanceAccumulator],
        stats: ProjectionStats,
    ) -> None:
        for key in sorted(accumulators):
            for built in accumulators[key].build(landing_state=TrustState.OBSERVED):
                # Merge rather than replace. `apply` accumulates a single event,
                # so writing the built record as-is would restate the whole
                # aggregate from that one event, and the live path would then
                # disagree with `rebuild` over the same journal.
                stored = self.repository.get(built.memory_id)
                memory = built
                if stored is not None:
                    merged = runs.merge_performance(stored, built)
                    if merged is None:
                        # Every contributing event is already counted; restating
                        # it would also record a second promotion for one rung.
                        continue
                    memory = merged
                self._write(memory, stats, update=stored is not None)
                # Performance aggregates are deterministic arithmetic over
                # trusted outcome events, so they reach `verified` without a
                # command having run.
                self._transition(
                    memory.model_copy(update={"trust_state": TrustState.VERIFIED}),
                    TrustState.OBSERVED,
                    TrustState.VERIFIED,
                    "promote.performance.deterministic_aggregate",
                    memory.source_event_ids,
                    None,
                    note="aggregated from recorded outcome events",
                )
                stats.promotions += 1


def _procedural_id(event: Event) -> str | None:
    """The identifier of the procedure this success produced, if it produced one.

    ``build_procedural`` writes nothing for an event carrying no command, and a
    link pointing at a memory that was never written is worse than no link.
    """
    if not str(event.payload.get("command", "")).strip():
        return None
    return verification.derive_memory_id(event.event_id, "procedural")


def _names_subject(memory: Memory, key: str) -> bool:
    """Whether a reviewer who named ``key`` named *this* record's subject.

    One subject can produce two keys. A record's ``subject_key`` is derived from
    whatever text identifies it, and for a lesson that is the subject and the
    finding together — a key no lookup built from the subject alone can
    reconstruct. Where a record also carries the subject it was filed under, that
    is compared as well, which is what lets a reviewer's verdict reach it.
    """
    if not key:
        return False
    if memory.subject_key == key:
        return True
    stated = str(memory.content.get("subject", "")).strip()
    return bool(stated) and invalidation.subject_key(stated) == key


def _resolves(gotcha: Memory, *, command: str, purpose: str) -> bool:
    """Whether a success is plausibly the fix for this failure.

    Deliberately narrow. A false negative leaves ``what_later_worked`` empty,
    which the warning already says out loud; a false positive records a
    fabricated fix and closes the signature, so the real one can never attach.
    Both arguments arrive normalised by the caller.
    """
    if failures.normalize_command(str(gotcha.content.get("command", ""))) == command:
        return True
    prior = str(gotcha.content.get("purpose", "")).strip().lower()
    return bool(purpose) and prior == purpose


def _branch_filter(project_id: str, branch: str) -> Any:
    from provalume.schemas.memories import MemoryFilter

    return MemoryFilter(
        project_id=project_id,
        branch=branch,
        include_terminal=False,
        current_only=True,
        limit=1000,
    )


def project_all(
    journal: Journal,
    repository: MemoryRepository,
    events: Iterable[Event],
    *,
    poisoning_threshold: float = 0.5,
) -> ProjectionStats:
    """Convenience: project a sequence of events."""
    projector = Projector(journal, repository, poisoning_threshold=poisoning_threshold)
    stats = ProjectionStats()
    accumulators: dict[str, runs.PerformanceAccumulator] = {}
    for event in events:
        projector._apply_one(event, stats, accumulators=accumulators)
        stats.events_processed += 1
    projector._flush_accumulators(accumulators, stats)
    return stats
