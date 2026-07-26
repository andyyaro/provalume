"""The retrieval engine: filter, score, explain.

Boundary 3 of the threat model. The order is a security property, not an
optimisation:

1. **Hard filters** authorise a candidate set — project, trust floor, terminal
   exclusion, validity, scope, commit validity.
2. **Scoring** reorders that set.

Nothing scored can appear that filtering did not authorise, and no score can
promote a record past a filter. This is what makes threat T6 survivable: an
adversarial embedding (or an adversarial BM25 match) can win the ranking contest
and still not be returned.
"""

from __future__ import annotations

from typing import Any

from provalume.retrieval import ranking
from provalume.schemas.memories import Memory, MemoryFilter, MemoryType
from provalume.schemas.provenance import Provenance, ResolutionStatus
from provalume.schemas.retrieval import (
    DEFAULT_RANKING_POLICY,
    Explanation,
    RankingPolicy,
    RecallQuery,
    RecallResult,
)
from provalume.schemas.scope import Applicability, Scope, ScopeLevel, specificity
from provalume.schemas.trust import TERMINAL_STATES, meets
from provalume.store import fts
from provalume.store.db import Database
from provalume.store.gitinfo import GitInfo, applicability_at
from provalume.store.repository import MemoryRepository

#: Event ids looked up per statement when resolving provenance for a candidate
#: set. Well under SQLite's variable limit, so a wide query cannot fail on the
#: bind count alone.
_EVENT_LOOKUP_CHUNK = 400


class RetrievalEngine:
    """Lexical retrieval with governance filtering and explainable ranking."""

    def __init__(
        self,
        db: Database,
        repository: MemoryRepository,
        *,
        policy: RankingPolicy | None = None,
        git: GitInfo | None = None,
    ) -> None:
        self.db = db
        self.repository = repository
        self.policy = policy or DEFAULT_RANKING_POLICY
        self.git = git

    # -- candidate selection ----------------------------------------------

    def _candidates(self, query: RecallQuery) -> tuple[list[Memory], dict[str, float]]:
        """Authorised candidates, plus normalised lexical scores.

        Two paths. With query text, FTS5 supplies both the candidate set and BM25
        scores. Without it, structured filtering supplies the candidates and every
        lexical score is 0.0 — a browse, not a search, and the other components
        do the ordering.
        """
        expression = fts.build_query(query.query)

        if not expression:
            spec = self._filter_for(query)
            return self.repository.find(spec), {}

        # FTS gives us rowids and bm25 scores; the governance filters are applied
        # afterwards in Python because they involve Git ancestry and scope logic
        # that does not belong in SQL.
        rows = self.db.query(
            "SELECT m.memory_id AS memory_id, bm25(memories_fts) AS raw "
            "FROM memories_fts f "
            "JOIN memories m ON m.rowid = f.rowid "
            "WHERE memories_fts MATCH ? AND m.project_id = ? "
            "ORDER BY raw LIMIT ?",
            (expression, query.project_id, self.policy.candidate_cap),
        )
        if not rows:
            return [], {}

        ids = tuple(str(r["memory_id"]) for r in rows)
        raw = [float(r["raw"]) for r in rows]
        normalised = fts.normalize_bm25(raw)
        lexical = dict(zip(ids, normalised, strict=True))

        by_id = {m.memory_id: m for m in self.repository.get_many(ids)}
        # Preserve FTS ordering so that, all else equal, ordering is stable.
        return [by_id[i] for i in ids if i in by_id], lexical

    def _filter_for(self, query: RecallQuery) -> MemoryFilter:
        # ``memory_types`` is deliberately absent: on this filter it would become
        # ``memory_type IN (...)``, a hard exclusion, while the FTS path treats
        # type as a ranking nudge (:meth:`_passes_filters`). One parameter must
        # not mean two different things depending on whether the caller happened
        # to supply query text.
        return MemoryFilter(
            project_id=query.project_id,
            min_trust=query.min_trust,
            include_terminal=query.include_terminal,
            current_only=not query.include_terminal,
            run_id=query.run_id,
            task_id=query.task_id,
            limit=self.policy.candidate_cap,
        )

    # -- filtering ---------------------------------------------------------

    def _passes_filters(self, memory: Memory, query: RecallQuery) -> tuple[bool, list[str], str]:
        """Whether a candidate is authorised, which filters it passed, and why not.

        Returning the passed-filter list is not bookkeeping: it is what
        ``explain`` shows to justify a result's presence, and the rejection reason
        is what makes a *missing* result diagnosable.
        """
        passed: list[str] = []

        # project_id: the one filter with no bypass, anywhere (threat T9).
        if memory.scope.project_id != query.project_id:
            return False, passed, "different project"
        passed.append("project_id matches")

        if memory.trust_state in TERMINAL_STATES:
            if not query.include_terminal:
                return False, passed, f"terminal state ({memory.trust_state})"
            passed.append(f"terminal state included by request ({memory.trust_state})")
        else:
            if not meets(memory.trust_state, query.min_trust):
                return (
                    False,
                    passed,
                    f"trust {memory.trust_state} is below the {query.min_trust} floor",
                )
            passed.append(f"trust {memory.trust_state} meets the {query.min_trust} floor")

        if not query.include_terminal and not memory.is_current:
            return False, passed, "no longer current (invalid_at is set)"
        if memory.is_current:
            passed.append("still current")

        if query.memory_types and memory.memory_type not in query.memory_types:
            # A soft filter: type is a ranking nudge, not an exclusion, so a
            # non-requested type stays in the set at a reduced weight.
            passed.append(f"type {memory.memory_type} not requested (ranked lower)")
        elif query.memory_types:
            passed.append(f"type {memory.memory_type} requested")

        for excluded in query.exclude_scopes:
            if excluded and excluded in memory.scope.describe():
                return False, passed, f"scope excluded by request ({excluded})"
        if query.exclude_scopes:
            passed.append("not in any excluded scope")

        return True, passed, ""

    def _unresolved_provenance(self, memories: list[Memory]) -> set[str]:
        """Ids of records citing at least one event absent from the journal.

        A record that claims evidence which does not exist is the forged-
        provenance signature (threat T15), and the read path has to be able to say
        so: a warning on the result and a rollup line on the digest. Answered with
        one batched existence check for the whole candidate set rather than the
        full :meth:`provenance` assembly, which costs a journal round-trip per
        record and is what ``explain`` is for.

        A record citing no events at all is not unresolved. It claims nothing.
        """
        wanted = sorted({eid for memory in memories for eid in memory.source_event_ids})
        if not wanted:
            return set()

        present: set[str] = set()
        for start in range(0, len(wanted), _EVENT_LOOKUP_CHUNK):
            chunk = tuple(wanted[start : start + _EVENT_LOOKUP_CHUNK])
            # Only the placeholder count is interpolated; every id is bound.
            placeholders = ", ".join("?" for _ in chunk)
            sql = (
                "SELECT event_id FROM events "  # noqa: S608  # nosec B608
                f"WHERE event_id IN ({placeholders})"
            )
            present.update(str(row["event_id"]) for row in self.db.query(sql, chunk))

        return {
            memory.memory_id
            for memory in memories
            if any(eid not in present for eid in memory.source_event_ids)
        }

    # -- main entry point --------------------------------------------------

    def recall(self, query: RecallQuery) -> list[RecallResult]:
        """Run a query and return ranked, explained results."""
        candidates, lexical_scores = self._candidates(query)
        if not candidates:
            return []

        query_scope = Scope(
            level=ScopeLevel.BRANCH if query.branch else ScopeLevel.REPOSITORY,
            project_id=query.project_id,
            repository_id=query.repository_id,
            branch=query.branch,
            run_id=query.run_id,
            task_id=query.task_id,
            agent_profile=query.agent_profile,
        )
        contradicted = self.repository.contradicted_ids(query.project_id)
        unresolved_provenance = self._unresolved_provenance(candidates)

        scored: list[tuple[tuple[float, str, str], RecallResult]] = []

        for memory in candidates:
            ok, passed, _reason = self._passes_filters(memory, query)
            if not ok:
                continue

            scope_score, scope_applicability = specificity(memory.scope, query_scope)
            applicability, applicability_reason = applicability_at(
                record_commit=memory.commit_sha,
                query_commit=query.commit_sha,
                git=self.git,
                scope_applicability=scope_applicability,
            )
            passed.append(f"applicability: {applicability}")

            has_contradiction = memory.memory_id in contradicted
            breakdown = ranking.score(
                memory,
                policy=self.policy,
                lexical=lexical_scores.get(memory.memory_id, 0.0),
                scope_specificity=scope_score,
                requested_types=query.memory_types,
                has_contradiction=has_contradiction,
                as_of=query.as_of,
            )

            provenance_summary = self._provenance_summary(memory)
            resolution_present = bool(memory.content.get("resolution"))

            explanation = Explanation(
                reasons=ranking.build_reasons(
                    memory,
                    breakdown=breakdown,
                    applicability=applicability,
                    applicability_reason=applicability_reason,
                    query_branch=query.branch,
                    has_contradiction=has_contradiction,
                    resolution_present=resolution_present,
                    provenance_summary=provenance_summary,
                ),
                filters_passed=tuple(passed),
                breakdown=breakdown,
                applicability=applicability,
                warnings=ranking.build_warnings(
                    memory,
                    applicability=applicability,
                    has_contradiction=has_contradiction,
                    provenance_resolved=memory.memory_id not in unresolved_provenance,
                ),
            )

            result = RecallResult(
                memory_id=memory.memory_id,
                memory_type=memory.memory_type,
                text=memory.text,
                content=memory.content,
                trust_state=memory.trust_state,
                freshness=memory.freshness,
                scope=memory.scope.describe(),
                commit_sha=memory.commit_sha,
                recorded_at=memory.recorded_at,
                valid_at=memory.valid_at,
                invalid_at=memory.invalid_at,
                score=breakdown.total,
                explanation=explanation,
                provenance_summary=provenance_summary,
                presentable_as_current_truth=(
                    memory.presentable_as_current_truth and applicability is Applicability.CURRENT
                ),
            )
            scored.append((ranking.sort_key(memory, breakdown), result))

        scored.sort(key=lambda pair: pair[0])
        results = []
        for rank_index, (_key, result) in enumerate(scored[: query.limit], start=1):
            results.append(result.model_copy(update={"rank": rank_index}))

        if results:
            self.repository.touch(tuple(r.memory_id for r in results))
        return results

    def _provenance_summary(self, memory: Memory) -> str:
        """One-line evidence summary, built from the record's own fields.

        Deliberately cheap: it reads the memory's denormalised evidence states
        rather than assembling a full :class:`Provenance`, because that would mean
        a journal round-trip per result. ``explain`` does the expensive version
        for a single record.
        """
        bits: list[str] = []
        if memory.verification_state.value == "passed":
            command = memory.content.get("command")
            bits.append(f"verified by `{command}`" if command else "verification passed")
        elif memory.verification_state.value == "failed":
            command = memory.content.get("command")
            bits.append(f"failed `{command}`" if command else "verification failed")
        if memory.review_state.value == "approved":
            bits.append("approved in independent review")
        elif memory.review_state.value == "rejected":
            bits.append("rejected in review")
        if memory.is_landed:
            sha = (memory.commit_sha or "")[:12]
            bits.append(f"landed in {sha}" if sha else "landed in history")
        occurrences = memory.content.get("occurrences")
        if isinstance(occurrences, int) and occurrences > 1:
            bits.append(f"seen {occurrences} times")
        return "; ".join(bits)

    # -- provenance --------------------------------------------------------

    def provenance(self, memory_id: str, journal: Any) -> Provenance | None:
        """Assemble the full evidence chain for one memory.

        Built on demand rather than stored, so it always reflects the current
        journal. A stored copy would be a second source of truth that could drift
        from the events it claims to summarise.
        """
        from provalume.schemas.events import EventType
        from provalume.schemas.provenance import (
            DecisionEvidence,
            IntegrationEvidence,
            ReviewEvidence,
            VerificationEvidence,
        )
        from provalume.schemas.trust import IntegrationState, ReviewState
        from provalume.writers.reviews import is_independent

        memory = self.repository.get(memory_id)
        if memory is None:
            return None

        events = journal.get_many(memory.source_event_ids)
        found_ids = {e.event_id for e in events}
        missing = [eid for eid in memory.source_event_ids if eid not in found_ids]

        verifications: list[VerificationEvidence] = []
        reviews: list[ReviewEvidence] = []
        integrations: list[IntegrationEvidence] = []
        decisions: list[DecisionEvidence] = []

        for event in events:
            kind = event.event_type
            if kind in {
                EventType.VERIFICATION_PASSED,
                EventType.VERIFICATION_FAILED,
                EventType.COMMAND_SUCCEEDED,
                EventType.COMMAND_FAILED,
            }:
                verifications.append(
                    VerificationEvidence(
                        event_id=event.event_id,
                        command=str(event.payload.get("command", "")),
                        passed=kind in {EventType.VERIFICATION_PASSED, EventType.COMMAND_SUCCEEDED},
                        exit_code=event.payload.get("exit_code"),
                        failure_signature=memory.content.get("failure_signature"),
                        recorded_at=event.recorded_at,
                        source=event.source,
                        excerpt=str(event.payload.get("excerpt", ""))[:8192],
                    )
                )
            elif kind in {
                EventType.REVIEW_APPROVED,
                EventType.REVIEW_REJECTED,
                EventType.REVIEW_CHANGES_REQUESTED,
            }:
                reviewer = str(event.payload.get("reviewer", event.agent_profile or ""))
                verdict = {
                    EventType.REVIEW_APPROVED: ReviewState.APPROVED,
                    EventType.REVIEW_REJECTED: ReviewState.REJECTED,
                    EventType.REVIEW_CHANGES_REQUESTED: ReviewState.CHANGES_REQUESTED,
                }[kind]
                raw_findings = event.payload.get("findings", [])
                findings = (
                    tuple(str(f) for f in raw_findings)
                    if isinstance(raw_findings, (list, tuple))
                    else ()
                )
                reviews.append(
                    ReviewEvidence(
                        event_id=event.event_id,
                        reviewer=reviewer,
                        verdict=verdict,
                        independent=is_independent(reviewer=reviewer, author=memory.author_agent),
                        findings=findings,
                        recorded_at=event.recorded_at,
                        source=event.source,
                    )
                )
            elif kind in {EventType.INTEGRATION_LANDED, EventType.INTEGRATION_REVERTED}:
                sha = event.commit_sha or ""
                resolved = ResolutionStatus.UNRESOLVABLE
                if self.git is not None and self.git.available and sha:
                    resolved = (
                        ResolutionStatus.RESOLVED
                        if self.git.commit_exists(sha)
                        else ResolutionStatus.UNRESOLVABLE
                    )
                integrations.append(
                    IntegrationEvidence(
                        event_id=event.event_id,
                        commit_sha=sha,
                        branch=event.branch,
                        state=(
                            IntegrationState.REVERTED
                            if kind is EventType.INTEGRATION_REVERTED
                            else IntegrationState.INTEGRATED_RUN
                        ),
                        recorded_at=event.recorded_at,
                        source=event.source,
                        resolution=resolved,
                    )
                )
            elif kind is EventType.HUMAN_DECISION:
                raw_rejected = event.payload.get("rejected", [])
                decisions.append(
                    DecisionEvidence(
                        event_id=event.event_id,
                        selected=str(event.payload.get("selected", "")),
                        rejected=(
                            tuple(str(r) for r in raw_rejected)
                            if isinstance(raw_rejected, (list, tuple))
                            else ()
                        ),
                        rationale=str(event.payload.get("rationale", "")),
                        authority=str(event.payload.get("authority", "")),
                        consequences=str(event.payload.get("consequences", "")),
                        recorded_at=event.recorded_at,
                        source=event.source,
                    )
                )

        # A referenced event that is absent from the journal is BROKEN, not
        # merely unresolvable: the record claims evidence that does not exist,
        # which is the forged-provenance signature (threat T15).
        if missing:
            resolution = ResolutionStatus.BROKEN
            detail = f"{len(missing)} referenced event(s) absent from the journal"
        elif self.git is None or not self.git.available:
            resolution = ResolutionStatus.UNRESOLVABLE
            detail = "no repository available to resolve commit provenance"
        elif memory.commit_sha and not self.git.commit_exists(memory.commit_sha):
            resolution = ResolutionStatus.UNRESOLVABLE
            detail = f"commit {memory.commit_sha[:12]} is not present in the repository"
        else:
            resolution = ResolutionStatus.RESOLVED
            detail = "all referenced events and commits resolved"

        successor = self.repository.links_to(memory_id, link_type="supersedes")
        resolved_by = self.repository.links_from(memory_id, link_type="resolved_by")

        return Provenance(
            memory_id=memory_id,
            trust_state=memory.trust_state,
            verification_state=memory.verification_state,
            review_state=memory.review_state,
            integration_state=memory.integration_state,
            source_event_ids=memory.source_event_ids,
            verifications=tuple(verifications),
            reviews=tuple(reviews),
            integrations=tuple(integrations),
            decisions=tuple(decisions),
            author_agent=memory.author_agent,
            adapter=memory.adapter,
            model=memory.model,
            effort=memory.effort,
            supersedes_id=memory.supersedes_id,
            superseded_by_id=successor[0]["from_id"] if successor else None,
            resolves_gotcha_id=resolved_by[0]["to_id"] if resolved_by else None,
            resolution=resolution,
            resolution_detail=detail,
            transitions=tuple(self.repository.transitions_for(memory_id)),
        )


def types_for_intent(intent: str) -> tuple[MemoryType, ...]:
    """Sensible category sets for common intents.

    Progressive disclosure: a caller should get useful results without learning
    the taxonomy first, while the full filter stays available underneath.
    """
    return {
        "planning": (MemoryType.DECISION, MemoryType.SEMANTIC, MemoryType.GOTCHA),
        "debugging": (MemoryType.GOTCHA, MemoryType.EPISODIC, MemoryType.PROCEDURAL),
        "howto": (MemoryType.PROCEDURAL, MemoryType.SEMANTIC),
        "facts": (MemoryType.SEMANTIC,),
        "failures": (MemoryType.GOTCHA,),
        "decisions": (MemoryType.DECISION,),
        "agents": (MemoryType.PERFORMANCE,),
    }.get(intent, ())
