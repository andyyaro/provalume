"""Memory records: the projections that agents actually read.

Six categories, each with its own promotion ceiling, because the evidence that
would justify trusting "this command works" differs from what would justify
"this is what the project currently is" (ADR-0004).

Every record carries both structured ``content`` and rendered ``text``.
Deliberate: ``content`` is what policy and filters reason over, ``text`` is what
enters a digest. Deriving one from the other at read time would make ranking
depend on formatting, and would make a rendering change invisible to the
determinism tests.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from provalume import _ids, _time
from provalume.schemas.freshness import DEFAULT_FRESHNESS, FreshnessState
from provalume.schemas.scope import Scope
from provalume.schemas.trust import (
    CURRENT_TRUTH_STATE,
    IntegrationState,
    ReviewState,
    Source,
    TrustState,
    VerificationState,
)

#: Transitions are frequently created several times within one millisecond — a
#: three-rung promotion is three rows with identical timestamps. A plain ULID
#: leaves their relative order to chance, which makes lifecycle history print
#: scrambled. A monotonic factory keeps insertion order recoverable from the ID.
_TRANSITION_IDS = _ids.MonotonicIdFactory()

MEMORY_SCHEMA_VERSION: Final = 1

MAX_MEMORY_TEXT_CHARS: Final = 8_192
MAX_MEMORY_CONTENT_BYTES: Final = 64 * 1024


class MemoryType(StrEnum):
    """The six persistent categories. Working memory is not stored — it is the
    digest composed at query time."""

    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    DECISION = "decision"
    GOTCHA = "gotcha"
    PERFORMANCE = "performance"


#: Highest trust state each category can reach *without* landed history.
#: Semantic is the strictest because it is the only category asserting what
#: currently *is*: a command passing in one worktree does not change the project.
TYPE_CEILING_WITHOUT_INTEGRATION: Final[dict[MemoryType, TrustState]] = {
    MemoryType.EPISODIC: TrustState.VERIFIED,
    MemoryType.SEMANTIC: TrustState.REVIEWED,
    MemoryType.PROCEDURAL: TrustState.VERIFIED,
    MemoryType.DECISION: TrustState.INTEGRATED,
    MemoryType.GOTCHA: TrustState.VERIFIED,
    MemoryType.PERFORMANCE: TrustState.VERIFIED,
}

#: Categories that must reach INTEGRATED before being presented as current
#: project truth without a qualifying label.
REQUIRES_INTEGRATION_FOR_TRUTH: Final[frozenset[MemoryType]] = frozenset({MemoryType.SEMANTIC})

#: Categories for which INTEGRATED is meaningless. An episode happened regardless
#: of what landed; a statistic does not land in a commit.
NEVER_INTEGRATED: Final[frozenset[MemoryType]] = frozenset(
    {MemoryType.EPISODIC, MemoryType.PERFORMANCE}
)

#: Categories that *assert something about the codebase* and can therefore be
#: reviewed and landed. Everything else records what happened.
#:
#: The distinction matters more than it looks. Without it, a review approving a
#: fix gets stamped onto every record sharing the attempt — including the failure
#: that prompted the fix — so a gotcha ends up reading "approved by reviewer-2"
#: and, once the branch merges, "integrated". Both are false: the reviewer
#: approved the fix, and what landed was the fix. The failure is still a failure.
CLAIM_TYPES: Final[frozenset[MemoryType]] = frozenset(
    {MemoryType.SEMANTIC, MemoryType.PROCEDURAL, MemoryType.DECISION}
)

#: Categories that record an occurrence. Review and integration verdicts do not
#: apply to them by attempt association; a reviewer must name the subject
#: explicitly to confirm a finding.
RECORD_TYPES: Final[frozenset[MemoryType]] = frozenset(
    {MemoryType.EPISODIC, MemoryType.GOTCHA, MemoryType.PERFORMANCE}
)

#: Recency half-life in days, per category (ADR-0008). A verified command stays
#: verified until something changes it; last sprint's episode does not.
RECENCY_HALF_LIFE_DAYS: Final[dict[MemoryType, float]] = {
    MemoryType.EPISODIC: 14.0,
    MemoryType.PERFORMANCE: 30.0,
    MemoryType.SEMANTIC: 60.0,
    MemoryType.GOTCHA: 90.0,
    MemoryType.PROCEDURAL: 180.0,
    MemoryType.DECISION: 365.0,
}


class Memory(BaseModel):
    """One persistent memory record."""

    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(min_length=1, max_length=64)
    schema_version: int = MEMORY_SCHEMA_VERSION
    memory_type: MemoryType

    content: dict[str, Any] = Field(default_factory=dict)
    text: str = Field(max_length=MAX_MEMORY_TEXT_CHARS)

    scope: Scope

    # --- Provenance ------------------------------------------------------
    run_id: str | None = Field(default=None, max_length=128)
    task_id: str | None = Field(default=None, max_length=128)
    attempt_id: str | None = Field(default=None, max_length=128)
    source_event_ids: tuple[str, ...] = ()
    source: Source
    author_agent: str | None = Field(default=None, max_length=256)
    adapter: str | None = Field(default=None, max_length=256)
    model: str | None = Field(default=None, max_length=256)
    effort: str | None = Field(default=None, max_length=64)
    commit_sha: str | None = Field(default=None, max_length=64)

    # --- Bi-temporal validity (ADR-0009) ---------------------------------
    valid_at: str
    invalid_at: str | None = None
    recorded_at: str

    # --- Lifecycle -------------------------------------------------------
    supersedes_id: str | None = Field(default=None, max_length=64)
    trust_state: TrustState = TrustState.QUARANTINED
    verification_state: VerificationState = VerificationState.UNKNOWN
    review_state: ReviewState = ReviewState.NONE
    integration_state: IntegrationState = IntegrationState.NONE

    # --- Freshness (ADR-0020) --------------------------------------------
    # Orthogonal to trust: does landed code still support this record's
    # evidence? `unverifiable` until a blast radius earns `current`; a
    # machine observation, never a judgement, and never able to move trust.
    freshness: FreshnessState = DEFAULT_FRESHNESS

    # --- Usage -----------------------------------------------------------
    access_count: int = Field(default=0, ge=0)
    last_accessed_at: str | None = None

    # --- Integrity and safety --------------------------------------------
    content_hash: str = ""
    redaction: dict[str, Any] = Field(default_factory=dict)
    poisoning_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    poisoning_matches: tuple[str, ...] = ()

    # --- Subject key, for contradiction detection (ADR-0009) -------------
    # A normalised key identifying *what the record is about*. Two current
    # semantic records in one scope with the same subject key and differing
    # content are a contradiction. Deliberately literal rather than semantic:
    # matching by meaning would need an LLM in the read path (ADR-0007), and a
    # fabricated contradiction demotes a correct fact.
    subject_key: str = Field(default="", max_length=512)

    @field_validator("valid_at", "invalid_at", "recorded_at", "last_accessed_at")
    @classmethod
    def _canonical_timestamp(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _time.normalize(value)

    @classmethod
    def create(
        cls,
        *,
        memory_type: MemoryType,
        text: str,
        scope: Scope,
        source: Source,
        content: dict[str, Any] | None = None,
        memory_id: str | None = None,
        recorded_at: str | None = None,
        valid_at: str | None = None,
        **fields: Any,
    ) -> Memory:
        """Build a memory with identity and timestamps applied.

        ``content_hash`` is left empty; the writer fills it once ``content`` and
        ``text`` are final, so the hash always covers what is actually stored.
        """
        stamp = recorded_at or _time.now()
        return cls(
            memory_id=memory_id or _ids.new_id(),
            memory_type=memory_type,
            text=text,
            scope=scope,
            source=source,
            content=content or {},
            recorded_at=stamp,
            valid_at=valid_at or stamp,
            **fields,
        )

    # --- Derived predicates ----------------------------------------------

    @property
    def is_current(self) -> bool:
        """Whether validity has not been closed off.

        Time-independent: an ``invalid_at`` in the future is still a closed
        record, because the invalidation is a recorded decision rather than a
        prediction.
        """
        return self.invalid_at is None

    @property
    def is_landed(self) -> bool:
        return self.integration_state in {
            IntegrationState.INTEGRATED_RUN,
            IntegrationState.ACCEPTED_USER,
        }

    @property
    def presentable_as_current_truth(self) -> bool:
        """Whether this may be shown as current project fact *without* a label.

        Semantic records need landed history; everything else needs only to be
        current and non-terminal. This is the single check that stops a
        rejected-branch fact reading like established truth.
        """
        from provalume.schemas.trust import is_terminal

        if is_terminal(self.trust_state) or not self.is_current:
            return False
        if self.memory_type in REQUIRES_INTEGRATION_FOR_TRUTH:
            return self.trust_state is CURRENT_TRUTH_STATE and self.is_landed
        return True

    @property
    def half_life_days(self) -> float:
        return RECENCY_HALF_LIFE_DAYS[self.memory_type]

    def ceiling_without_integration(self) -> TrustState:
        return TYPE_CEILING_WITHOUT_INTEGRATION[self.memory_type]


class MemoryFilter(BaseModel):
    """Structured filter for reading memories. No SQL crosses this boundary."""

    model_config = ConfigDict(extra="forbid")

    project_id: str | None = None
    memory_types: tuple[MemoryType, ...] = ()
    trust_states: tuple[TrustState, ...] = ()
    min_trust: TrustState | None = None
    include_terminal: bool = False
    current_only: bool = True
    branch: str | None = None
    commit_sha: str | None = None
    run_id: str | None = None
    task_id: str | None = None
    attempt_id: str | None = None
    subject_key: str | None = None
    author_agent: str | None = None
    limit: int = Field(default=50, ge=1, le=1_000)
    offset: int = Field(default=0, ge=0)


class Transition(BaseModel):
    """One recorded lifecycle transition.

    Refusals are recorded as well as approvals (``allowed=False``). A promotion
    attempt that silently vanishes is exactly what an attacker wants, so the
    refusal and the rule that produced it are both durable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    transition_id: str = Field(min_length=1, max_length=64)
    memory_id: str = Field(min_length=1, max_length=64)
    from_state: TrustState
    to_state: TrustState
    policy_rule: str = Field(min_length=1, max_length=256)
    evidence_event_ids: tuple[str, ...] = ()
    actor: str = Field(max_length=256)
    actor_source: Source
    recorded_at: str
    scope: str = Field(default="", max_length=512)
    allowed: bool = True
    note: str = Field(default="", max_length=2_048)

    @field_validator("recorded_at")
    @classmethod
    def _canonical_timestamp(cls, value: str) -> str:
        return _time.normalize(value)

    @classmethod
    def create(
        cls,
        *,
        memory_id: str,
        from_state: TrustState,
        to_state: TrustState,
        policy_rule: str,
        actor: str,
        actor_source: Source,
        evidence_event_ids: tuple[str, ...] = (),
        scope: str = "",
        allowed: bool = True,
        note: str = "",
    ) -> Transition:
        return cls(
            transition_id=_TRANSITION_IDS.new_id(),
            memory_id=memory_id,
            from_state=from_state,
            to_state=to_state,
            policy_rule=policy_rule,
            evidence_event_ids=evidence_event_ids,
            actor=actor,
            actor_source=actor_source,
            recorded_at=_time.now(),
            scope=scope,
            allowed=allowed,
            note=note,
        )
