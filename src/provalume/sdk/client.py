"""The public Python API.

Stable within a minor release series (ADR-0017). Everything reachable only
through ``provalume.store``, ``provalume.policy``, ``provalume.writers``,
``provalume.retrieval``, or ``provalume.interchange`` is internal.

The shape of this API encodes the trust model, so that misuse is awkward rather
than merely discouraged:

* :meth:`Provalume.propose` exists and lands records ``quarantined``.
* :meth:`Provalume.promote` requires an explicit non-agent actor and refuses
  otherwise.
* There is no method that writes a trusted record in one call.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from provalume import _time
from provalume.errors import IntegrityError, ProvalumeError, TrustError
from provalume.interchange import jsonl
from provalume.policy import promotion
from provalume.policy.admission import admit_event
from provalume.retrieval.digest import compose
from provalume.retrieval.lexical import RetrievalEngine
from provalume.retrieval.preflight import PreflightGate
from provalume.schemas.events import Event, EventFilter, EventType
from provalume.schemas.memories import Memory, MemoryFilter, MemoryType, Transition
from provalume.schemas.provenance import Provenance
from provalume.schemas.retrieval import (
    DEFAULT_RANKING_POLICY,
    Digest,
    PreflightResult,
    RankingPolicy,
    RecallQuery,
    RecallResult,
)
from provalume.schemas.trust import Source, TrustState
from provalume.store.db import Database, open_database
from provalume.store.fts import MAX_QUERY_CHARS
from provalume.store.gitinfo import GitInfo
from provalume.store.integrity import Auditor, AuditReport
from provalume.store.journal import Journal
from provalume.store.projections import ProjectionStats, Projector
from provalume.store.repository import MemoryRepository

#: Project-local by default. There is no `~/.provalume/`, and global scope is not
#: implemented (ADR-0016), so two projects cannot see each other's memory because
#: they are different files.
DEFAULT_DB_PATH = Path(".provalume") / "provalume.db"


class RecallResponse:
    """Results of a recall, with digest composition attached.

    A small wrapper rather than a bare list, so ``recall(...).digest(...)`` reads
    as one thought and the caller never has to re-plumb results into a composer.
    """

    __slots__ = ("query", "results")

    def __init__(self, results: list[RecallResult], query: RecallQuery) -> None:
        self.results = results
        self.query = query

    def __iter__(self) -> Any:
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)

    def __getitem__(self, index: int) -> RecallResult:
        return self.results[index]

    def __bool__(self) -> bool:
        return bool(self.results)

    def digest(
        self,
        *,
        char_budget: int | None = None,
        token_budget: int | None = None,
        include_reasons: bool = False,
    ) -> Digest:
        """Compose a budgeted digest. Always banner-first, always within budget."""
        return compose(
            self.results,
            char_budget=char_budget,
            token_budget=token_budget,
            include_reasons=include_reasons,
        )


class Provalume:
    """A Provalume database, and the operations available on it."""

    def __init__(
        self,
        db: Database,
        *,
        project_id: str,
        repository_id: str | None = None,
        policy: RankingPolicy | None = None,
        git: GitInfo | None = None,
        poisoning_threshold: float = 0.5,
        allow_blocking_preflight: bool = False,
    ) -> None:
        self.db = db
        self.project_id = project_id
        self.repository_id = repository_id
        self.policy = policy or DEFAULT_RANKING_POLICY
        self.git = git
        self.poisoning_threshold = poisoning_threshold

        self.journal = Journal(db)
        self.memories = MemoryRepository(db)
        self.projector = Projector(
            self.journal, self.memories, poisoning_threshold=poisoning_threshold
        )
        self.engine = RetrievalEngine(db, self.memories, policy=self.policy, git=git)
        self.gate = PreflightGate(self.memories, git=git, allow_blocking=allow_blocking_preflight)
        self.auditor = Auditor(db, self.journal, self.memories)

    # -- construction ------------------------------------------------------

    @classmethod
    def open(
        cls,
        path: Path | str | None = None,
        *,
        project_id: str | None = None,
        repository_id: str | None = None,
        root: Path | str | None = None,
        policy: RankingPolicy | None = None,
        use_git: bool = True,
        poisoning_threshold: float = 0.5,
        allow_blocking_preflight: bool = False,
    ) -> Provalume:
        """Open (creating and migrating if needed) a Provalume database.

        ``project_id`` defaults to the repository identity when Git is available,
        and to the directory name otherwise. Deriving it rather than requiring it
        keeps the common case to one call, and the derived value is stable —
        which matters because ``project_id`` is the isolation boundary (threat T9).
        """
        base = Path(root) if root is not None else Path.cwd()
        git = GitInfo(base) if use_git else None

        resolved_project = project_id
        if resolved_project is None:
            if git is not None and git.available:
                resolved_project = git.repository_id() or base.resolve().name
            else:
                resolved_project = base.resolve().name
        if not resolved_project:
            msg = "project_id could not be determined; pass it explicitly"
            raise ProvalumeError(msg)

        db_path = Path(path) if path is not None else base / DEFAULT_DB_PATH
        database = open_database(db_path)

        resolved_repo = repository_id
        if resolved_repo is None and git is not None and git.available:
            resolved_repo = git.repository_id()

        return cls(
            database,
            project_id=resolved_project,
            repository_id=resolved_repo,
            policy=policy,
            git=git,
            poisoning_threshold=poisoning_threshold,
            allow_blocking_preflight=allow_blocking_preflight,
        )

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> Provalume:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- writing -----------------------------------------------------------

    def record_event(
        self,
        event_type: EventType | str,
        *,
        source: Source | str = Source.KERNEL,
        payload: dict[str, Any] | None = None,
        project: bool = True,
        **fields: Any,
    ) -> Event:
        """Record an event and project it.

        The only way to write to the journal. Runs the full admission pipeline —
        validation, size caps, redaction, poisoning scan — before anything
        durable happens, which is why :meth:`Event.create` alone cannot persist.
        """
        defaults: dict[str, Any] = {}
        if self.repository_id and "repository_id" not in fields:
            defaults["repository_id"] = self.repository_id
        if self.git is not None and self.git.available:
            if "branch" not in fields:
                branch = self.git.current_branch()
                if branch:
                    defaults["branch"] = branch
            if "commit_sha" not in fields:
                commit = self.git.current_commit()
                if commit:
                    defaults["commit_sha"] = commit

        event = Event.create(
            event_type=EventType(event_type),
            project_id=self.project_id,
            source=Source(source),
            payload=payload or {},
            **{**defaults, **fields},
        )
        admitted = admit_event(event, poisoning_threshold=self.poisoning_threshold)
        stored = self.journal.append(admitted.event)
        if project and stored.inserted:
            self.projector.apply(stored.event)
        return stored.event

    def record_verification(
        self,
        *,
        command: str,
        passed: bool,
        exit_code: int | None = None,
        excerpt: str = "",
        purpose: str = "",
        resolves_signature: str = "",
        **fields: Any,
    ) -> Event:
        """Record a verification result — the primary evidence event.

        ``resolves_signature`` names a failure signature this success resolves.
        Supply it when a command that previously failed now passes: without it,
        resolution is only inferred within the same task or run, and a failure
        that is escalated, fixed by a human, and re-run in a *later* run never
        gets linked. The gotcha then warns forever about something that was
        fixed, and ``what_later_worked`` stays empty.
        """
        return self.record_event(
            EventType.VERIFICATION_PASSED if passed else EventType.VERIFICATION_FAILED,
            source=Source.KERNEL,
            payload={
                "command": command,
                "exit_code": exit_code if exit_code is not None else (0 if passed else 1),
                "excerpt": excerpt,
                "purpose": purpose,
                **({"resolves_signature": resolves_signature} if resolves_signature else {}),
                **({"error_kind": fields.pop("error_kind")} if "error_kind" in fields else {}),
            },
            **fields,
        )

    def record_review(
        self,
        *,
        reviewer: str,
        approved: bool,
        subject: str = "",
        finding: str = "",
        findings: Sequence[str] = (),
        changes_requested: bool = False,
        **fields: Any,
    ) -> Event:
        """Record a review verdict.

        The reviewer's identity is recorded so independence can be checked at
        promotion time. A self-review never promotes, and the comparison happens
        there rather than here — recording a self-review is legitimate, promoting
        on one is not.
        """
        if approved:
            kind = EventType.REVIEW_APPROVED
        elif changes_requested:
            kind = EventType.REVIEW_CHANGES_REQUESTED
        else:
            kind = EventType.REVIEW_REJECTED
        return self.record_event(
            kind,
            source=Source.KERNEL,
            payload={
                "reviewer": reviewer,
                "subject": subject,
                "finding": finding,
                "findings": list(findings),
            },
            **fields,
        )

    def record_decision(
        self,
        *,
        selected: str,
        rejected: Sequence[str] = (),
        rationale: str = "",
        authority: str = "",
        question: str = "",
        consequences: str = "",
        **fields: Any,
    ) -> Event:
        """Record a human decision. ``source=human``, the highest authority."""
        return self.record_event(
            EventType.HUMAN_DECISION,
            source=Source.HUMAN,
            payload={
                "question": question,
                "selected": selected,
                "rejected": list(rejected),
                "rationale": rationale,
                "authority": authority,
                "consequences": consequences,
            },
            **fields,
        )

    def record_fact(
        self, *, statement: str, subject: str = "", changed: bool = False, **fields: Any
    ) -> Event:
        """Record a repository fact.

        ``changed=True`` supersedes the prior fact on the same subject rather than
        overwriting it, so history survives (ADR-0009).
        """
        return self.record_event(
            EventType.FACT_CHANGED if changed else EventType.FACT_OBSERVED,
            source=Source.KERNEL,
            payload={"statement": statement, "subject": subject, **fields.pop("extra", {})},
            **fields,
        )

    def record_integration(
        self,
        *,
        commit_sha: str,
        target: str = "run",
        branch: str | None = None,
        resolves_signature: str = "",
        **fields: Any,
    ) -> Event:
        """Record that work landed. The evidence semantic truth requires.

        ``resolves_signature`` names a failure this landing resolves. Prefer it
        to the same argument on :meth:`record_verification` whenever an
        orchestrator can supply it: a verification proves a command succeeded in
        some worktree, and worktrees are discarded for merge conflicts, rejected
        reviews and exhausted budgets. Only a landing proves the repository
        changed, which is what "what later worked" claims.
        """
        return self.record_event(
            EventType.INTEGRATION_LANDED,
            source=Source.KERNEL,
            payload={
                "target": target,
                "branch": branch or "",
                **({"resolves_signature": resolves_signature} if resolves_signature else {}),
            },
            commit_sha=commit_sha,
            # The envelope branch too, not only the payload. Without this the
            # event falls back to whatever branch the recording process happens
            # to have checked out — for an orchestrator that is the *base*
            # branch, so the row read "landed on main" while the payload
            # correctly named the integration branch. A consumer reading the
            # column rather than the payload drew the opposite conclusion.
            **({"branch": branch} if branch else {}),
            **fields,
        )

    def propose(
        self,
        *,
        text: str,
        memory_type: MemoryType | str = MemoryType.SEMANTIC,
        content: dict[str, Any] | None = None,
        agent: str = "",
        **fields: Any,
    ) -> Event:
        """Submit an agent-proposed memory candidate.

        Lands ``quarantined``, always. Nothing in the payload can change that:
        trust comes from ``source``, which this method fixes to ``agent`` (threat
        T2). Promotion requires deterministic evidence recorded elsewhere.
        """
        return self.record_event(
            EventType.AGENT_PROPOSAL,
            source=Source.AGENT,
            payload={
                "memory_type": MemoryType(memory_type).value,
                "text": text,
                "content": content or {},
            },
            agent_profile=agent or fields.pop("agent_profile", None),
            **fields,
        )

    # -- lifecycle ---------------------------------------------------------

    def promote(
        self,
        memory_id: str,
        target: TrustState | str,
        *,
        actor: str,
        actor_source: Source | str = Source.HUMAN,
        evidence_event_ids: Sequence[str] = (),
        note: str = "",
    ) -> Memory:
        """Advance a memory one rung, if its evidence allows.

        Refusals raise :class:`TrustError` **and** are recorded as a transition.
        A promotion attempt that vanishes silently is what an attacker wants.
        """
        memory = self.memories.get(memory_id)
        if memory is None:
            msg = f"no such memory: {memory_id}"
            raise ProvalumeError(msg)

        target_state = TrustState(target)
        source = Source(actor_source)
        evidence = tuple(
            self.journal.get_many(tuple(evidence_event_ids))
            if evidence_event_ids
            else self.journal.get_many(memory.source_event_ids)
        )

        decision = promotion.can_promote(
            memory,
            target_state,
            evidence=evidence,
            actor_source=source,
            poisoning_threshold=self.poisoning_threshold,
        )
        transition = Transition.create(
            memory_id=memory_id,
            from_state=memory.trust_state,
            to_state=target_state,
            policy_rule=decision.rule,
            actor=actor,
            actor_source=source,
            evidence_event_ids=decision.evidence_event_ids or tuple(e.event_id for e in evidence),
            scope=memory.scope.describe(),
            allowed=decision.allowed,
            note=note or decision.reason,
        )

        if not decision.allowed:
            self.memories.transition(memory, transition)
            raise TrustError(decision.reason, rule=decision.rule, memory_id=memory_id)

        review, integration = promotion.evidence_states(evidence)
        updates: dict[str, Any] = {"trust_state": target_state}
        if review.value != "none":
            updates["review_state"] = review
        if integration.value != "none":
            updates["integration_state"] = integration
        promoted = memory.model_copy(update=updates)
        self.memories.transition(promoted, transition)
        return promoted

    def reject(self, memory_id: str, *, actor: str, reason: str = "") -> Event:
        """Reject a memory permanently. Retained as negative experience."""
        return self.record_event(
            EventType.HUMAN_REJECTION,
            source=Source.HUMAN,
            payload={"memory_id": memory_id, "reason": reason, "actor": actor},
        )

    def invalidate(self, memory_id: str, *, actor: str = "", reason: str = "") -> Event:
        """Mark a fact as no longer true. History is retained."""
        return self.record_event(
            EventType.HUMAN_INVALIDATION,
            source=Source.HUMAN,
            payload={"memory_id": memory_id, "reason": reason, "actor": actor},
        )

    def supersede(self, old_id: str, *, statement: str, subject: str = "", **fields: Any) -> Event:
        """Replace a fact with a newer one. Both records persist."""
        return self.record_event(
            EventType.FACT_CHANGED,
            source=Source.KERNEL,
            payload={"statement": statement, "subject": subject, "replaces": old_id},
            **fields,
        )

    # -- reading -----------------------------------------------------------

    def recall(
        self,
        query: str = "",
        *,
        branch: str | None = None,
        commit_sha: str | None = None,
        memory_types: Sequence[MemoryType | str] = (),
        min_trust: TrustState | str = TrustState.OBSERVED,
        include_terminal: bool = False,
        limit: int = 10,
        as_of: str | None = None,
        **fields: Any,
    ) -> RecallResponse:
        """Retrieve ranked, explained memories."""
        resolved_branch = branch
        if resolved_branch is None and self.git is not None and self.git.available:
            resolved_branch = self.git.current_branch()
        resolved_commit = commit_sha
        if resolved_commit is None and self.git is not None and self.git.available:
            resolved_commit = self.git.current_commit()

        # Truncate rather than reject. An over-long query is a paste, not an
        # attack — the FTS layer already caps term count and length — and
        # erroring on it would turn a harmless mistake into a failed call.
        spec = RecallQuery(
            project_id=self.project_id,
            query=query[:MAX_QUERY_CHARS],
            repository_id=self.repository_id,
            branch=resolved_branch,
            commit_sha=resolved_commit,
            memory_types=tuple(MemoryType(t) for t in memory_types),
            min_trust=TrustState(min_trust),
            include_terminal=include_terminal,
            limit=limit,
            as_of=as_of,
            **fields,
        )
        return RecallResponse(self.engine.recall(spec), spec)

    def explain(self, memory_id: str) -> Provenance | None:
        """The full evidence chain for one memory.

        If this cannot tell you why a record is trusted, the record should not be
        trusted.
        """
        return self.engine.provenance(memory_id, self.journal)

    def preflight(
        self,
        *,
        command: str = "",
        error_kind: str = "",
        error_text: str = "",
        subsystem: str = "",
        files: Sequence[str] = (),
        branch: str | None = None,
        record: bool = True,
    ) -> PreflightResult:
        """Check whether a proposed action already failed.

        Warns; does not block by default. When ``record`` is set, a
        ``warning.shown`` event is written so the warning's usefulness can be
        measured rather than assumed.
        """
        resolved_branch = branch
        if resolved_branch is None and self.git is not None and self.git.available:
            resolved_branch = self.git.current_branch()
        commit = self.git.current_commit() if self.git and self.git.available else None

        result = self.gate.check(
            project_id=self.project_id,
            command=command,
            error_kind=error_kind,
            error_text=error_text,
            subsystem=subsystem,
            files=tuple(files),
            branch=resolved_branch,
            commit_sha=commit,
        )

        if record and result.matched:
            # Recorded through the projecting path even though nothing projects a
            # `warning.shown`. Projection is what advances the watermark, and
            # skipping it left `provalume audit` reporting "projections are
            # behind the journal" after every warning that matched — constantly,
            # in an orchestrator run, which devalues the one signal that would
            # surface a genuinely stale projection.
            event = self.record_event(
                EventType.WARNING_SHOWN,
                source=Source.KERNEL,
                payload={
                    "command": command,
                    "subsystem": subsystem,
                    "matches": [m.memory_id for m in result.matches],
                    "top_confidence": result.matches[0].confidence if result.matches else 0.0,
                    "blocked": result.should_block,
                },
            )
            return result.model_copy(update={"warning_event_id": event.event_id})
        return result

    def record_warning_outcome(
        self, *, warning_event_id: str, heeded: bool, false_positive: bool = False
    ) -> Event:
        """Record what happened after a warning.

        This is what makes warning usefulness measurable (eval scenario 19) rather
        than a matter of opinion. A gate nobody can evaluate is a gate nobody
        should trust.
        """
        if false_positive:
            kind = EventType.WARNING_FALSE_POSITIVE
        elif heeded:
            kind = EventType.WARNING_HEEDED
        else:
            kind = EventType.WARNING_IGNORED
        return self.record_event(
            kind,
            source=Source.KERNEL,
            payload={"warning_event_id": warning_event_id},
            causal_parent_event_id=warning_event_id,
        )

    def events(self, spec: EventFilter | None = None, **fields: Any) -> list[Event]:
        base = spec or EventFilter(project_id=self.project_id, **fields)
        return self.journal.find(base)

    def memory_records(self, spec: MemoryFilter | None = None, **fields: Any) -> list[Memory]:
        base = spec or MemoryFilter(project_id=self.project_id, **fields)
        return self.memories.find(base)

    # -- maintenance -------------------------------------------------------

    def rebuild(self, *, check_only: bool = False) -> ProjectionStats:
        """Rebuild every projection from the journal.

        ADR-0002's claim in executable form: if this cannot reproduce the
        projections, the journal is not really the source of truth.
        """
        if check_only:
            return self.projector.catch_up(project_id=self.project_id)
        return self.projector.rebuild(project_id=self.project_id)

    def audit(self, *, deep: bool = True) -> AuditReport:
        """Prove chain integrity, projection consistency, pragmas, and redaction."""
        return self.auditor.run(project_id=self.project_id, deep=deep)

    def export(
        self, directory: Path | str, *, signer: Any = None, audit_first: bool = True
    ) -> jsonl.ExportResult:
        """Export to JSONL.

        Refuses to run when audit finds unredacted credential patterns (threat
        T13). Export is the moment data leaves the machine, which makes it the
        last place to catch a leak — and the worst place to discover one later.
        """
        if audit_first:
            report = self.audit(deep=True)
            leaks = [f for f in report.errors if f.check == "credential_scan"]
            if leaks:
                msg = (
                    "refusing to export: audit found unredacted credential patterns "
                    f"({leaks[0].detail}). Fix or remove the affected records first."
                )
                raise ProvalumeError(msg)

        events = list(self.journal.iter_all(project_id=self.project_id))
        memories = list(self.memories.iter_all(project_id=self.project_id))
        transitions: list[dict[str, Any]] = []
        for memory in memories:
            transitions.extend(self.memories.transitions_for(memory.memory_id, limit=1000))

        return jsonl.export(
            directory=directory,
            events=events,
            memories=memories,
            transitions=transitions,
            signer=signer,
        )

    def import_records(
        self,
        directory: Path | str,
        *,
        allow_foreign_project: bool = False,
        quarantine_unknown: bool = False,
        verifier: Any = None,
        apply: bool = True,
    ) -> jsonl.ImportResult:
        """Import a JSONL export.

        Imported records arrive ``source=import`` with an ``observed`` ceiling.
        Their claimed trust states are discarded and re-derived locally from
        evidence that also imported and also validated (threat T17).

        A file is untrusted input, so its events cross into the journal through
        the same admission pipeline as a locally recorded one — validation, size
        caps, redaction, poisoning scan — run by :func:`jsonl.import_directory`
        so a record that fails is reported as an issue rather than aborting the
        import.

        Only events are stored. Memory and transition records are parsed and
        checked — that is where divergent supersessions are found — but the
        projections are rebuilt from the events that just landed (ADR-0002),
        which is why ``ImportResult.accepted`` counts events alone.
        """
        # Every event in the journal, not just this project's. `event_id` is a
        # global key — `Journal.append` looks it up without a project filter — so
        # a map scoped to one project reports "new" for an id another project
        # already holds, and the append then raises out of the whole batch.
        existing = {e.event_id: e.payload_hash for e in self.journal.iter_all()}
        result = jsonl.import_directory(
            directory,
            project_id=self.project_id,
            existing_event_hashes=existing,
            allow_foreign_project=allow_foreign_project,
            quarantine_unknown=quarantine_unknown,
            verifier=verifier,
            poisoning_threshold=self.poisoning_threshold,
        )
        if apply and result.events:
            self._append_imported(result)
            if result.events:
                self.projector.catch_up(project_id=self.project_id)
        return result

    def _append_imported(self, result: jsonl.ImportResult) -> None:
        """Append imported events, reporting a chain conflict instead of raising.

        The batch is one transaction, so one conflicting record would otherwise
        roll back every valid record in the file and surface as a traceback. A
        conflict is a normal import outcome (ADR-0011): it is reported and the
        rest still lands, which is why the retry appends one at a time.
        """
        try:
            self.journal.append_many(result.events)
        except IntegrityError:
            kept: list[Event] = []
            for event in result.events:
                try:
                    self.journal.append(event)
                except IntegrityError as exc:
                    result.conflicts.append(
                        jsonl.ImportIssue(0, jsonl.EVENTS_FILE, str(exc), event.event_id)
                    )
                else:
                    kept.append(event)
            result.events = kept

    def status(self) -> dict[str, Any]:
        """A summary suitable for `provalume status` or a health endpoint."""
        head, seq, count = self.journal.head()
        by_trust = {
            str(r["trust_state"]): int(r["n"])
            for r in self.db.query(
                "SELECT trust_state, COUNT(*) AS n FROM memories WHERE project_id = ? "
                "GROUP BY trust_state",
                (self.project_id,),
            )
        }
        by_type = {
            str(r["memory_type"]): int(r["n"])
            for r in self.db.query(
                "SELECT memory_type, COUNT(*) AS n FROM memories WHERE project_id = ? "
                "GROUP BY memory_type",
                (self.project_id,),
            )
        }
        return {
            "project_id": self.project_id,
            "repository_id": self.repository_id,
            "database": str(self.db.path),
            "schema_version": self.db.schema_version(),
            "events": count,
            "chain_head": head,
            "chain_seq": seq,
            "memories_by_trust": by_trust,
            "memories_by_type": by_type,
            "git_available": bool(self.git and self.git.available),
            "branch": self.git.current_branch() if self.git and self.git.available else None,
            "commit": self.git.current_commit() if self.git and self.git.available else None,
            "projection_seq": self.memories.projection_seq(),
            "generated_at": _time.now(),
        }
