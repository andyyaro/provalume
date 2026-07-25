"""The event journal's record type.

Events are the source of truth (ADR-0002). Every memory, index, digest, and
contradiction link is a projection derived from them, and can be dropped and
rebuilt.

Two properties this module is responsible for:

* **Immutability of meaning.** ``schema_version`` is stored on every event so a
  projection rebuild can apply the rules that were current when the event was
  recorded, rather than retroactively reinterpreting old evidence under new
  policy.
* **Size discipline.** Caps are enforced here, before anything reaches storage,
  because an oversized record can consume an entire digest budget (threat T25).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from provalume import _ids, _time
from provalume.schemas.trust import Source

#: Current event schema version. Bumping this is a compatibility event
#: (ADR-0017): readers must continue to accept every earlier version.
EVENT_SCHEMA_VERSION: Final = 1

#: Size caps, enforced at admission. Chosen to be generous for real payloads and
#: firm enough that no single record can dominate storage or a digest.
MAX_PAYLOAD_BYTES: Final = 256 * 1024
MAX_TEXT_FIELD_CHARS: Final = 8_192
MAX_IDENTIFIER_CHARS: Final = 256
MAX_BRANCH_CHARS: Final = 512
MAX_PATH_CHARS: Final = 4_096
MAX_COMMAND_CHARS: Final = 4_096
MAX_ERROR_EXCERPT_CHARS: Final = 8_192


class EventType(StrEnum):
    """The closed set of event types.

    Closed deliberately. Deterministic writers map event types to memory
    candidates (ADR-0007), so an open-ended type would mean an event nothing knows
    how to project — stored, and silently inert.
    """

    # --- Run and task lifecycle ------------------------------------------
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    ATTEMPT_STARTED = "attempt.started"
    ATTEMPT_COMPLETED = "attempt.completed"

    # --- Deterministic evidence ------------------------------------------
    VERIFICATION_PASSED = "verification.passed"
    VERIFICATION_FAILED = "verification.failed"
    COMMAND_SUCCEEDED = "command.succeeded"
    COMMAND_FAILED = "command.failed"

    # --- Review ----------------------------------------------------------
    REVIEW_APPROVED = "review.approved"
    REVIEW_REJECTED = "review.rejected"
    REVIEW_CHANGES_REQUESTED = "review.changes_requested"
    REVIEW_FINDING = "review.finding"

    # --- Human authority -------------------------------------------------
    HUMAN_DECISION = "human.decision"
    HUMAN_PROMOTION = "human.promotion"
    HUMAN_INVALIDATION = "human.invalidation"
    HUMAN_REJECTION = "human.rejection"

    # --- Git -------------------------------------------------------------
    INTEGRATION_LANDED = "integration.landed"
    INTEGRATION_REVERTED = "integration.reverted"
    COMMIT_RECORDED = "commit.recorded"
    BRANCH_REJECTED = "branch.rejected"

    # --- Facts -----------------------------------------------------------
    FACT_OBSERVED = "fact.observed"
    FACT_CHANGED = "fact.changed"

    # --- Agent-sourced, always untrusted ---------------------------------
    AGENT_PROPOSAL = "agent.proposal"
    AGENT_OBSERVATION = "agent.observation"
    AGENT_FAILURE_REPORT = "agent.failure_report"
    AGENT_OUTCOME_REPORT = "agent.outcome_report"

    # --- Retrieval feedback, for measuring whether warnings help ---------
    WARNING_SHOWN = "warning.shown"
    WARNING_HEEDED = "warning.heeded"
    WARNING_IGNORED = "warning.ignored"
    WARNING_FALSE_POSITIVE = "warning.false_positive"

    # --- Interface audit -------------------------------------------------
    MCP_CALL = "mcp.call"
    MCP_REFUSED = "mcp.refused"

    # --- Maintenance -----------------------------------------------------
    IMPORT_APPLIED = "import.applied"
    IMPORT_REJECTED = "import.rejected"


#: Event types that constitute deterministic evidence usable for promotion. An
#: event outside this set can never, by itself, raise a memory's trust state.
EVIDENCE_EVENT_TYPES: Final[frozenset[EventType]] = frozenset(
    {
        EventType.VERIFICATION_PASSED,
        EventType.VERIFICATION_FAILED,
        EventType.COMMAND_SUCCEEDED,
        EventType.COMMAND_FAILED,
        EventType.REVIEW_APPROVED,
        EventType.REVIEW_REJECTED,
        EventType.REVIEW_CHANGES_REQUESTED,
        EventType.HUMAN_DECISION,
        EventType.HUMAN_PROMOTION,
        EventType.HUMAN_INVALIDATION,
        EventType.HUMAN_REJECTION,
        EventType.INTEGRATION_LANDED,
        EventType.INTEGRATION_REVERTED,
        EventType.BRANCH_REJECTED,
    }
)

#: Event types whose content originates from an agent and is therefore hostile
#: until proven otherwise. These get the poisoning scan and a QUARANTINED landing.
AGENT_EVENT_TYPES: Final[frozenset[EventType]] = frozenset(
    {
        EventType.AGENT_PROPOSAL,
        EventType.AGENT_OBSERVATION,
        EventType.AGENT_FAILURE_REPORT,
        EventType.AGENT_OUTCOME_REPORT,
    }
)


class Event(BaseModel):
    """One immutable journal entry.

    Construct through :meth:`create` rather than directly, so identifiers,
    timestamps, and normalisation are applied consistently. The hash fields are
    filled by the journal at append time, because ``prev_event_hash`` depends on
    what is already stored and cannot be known by the caller.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(min_length=1, max_length=64)
    schema_version: int = EVENT_SCHEMA_VERSION
    event_type: EventType
    recorded_at: str
    occurred_at: str | None = None

    # --- Scope -----------------------------------------------------------
    project_id: str = Field(min_length=1, max_length=MAX_IDENTIFIER_CHARS)
    repository_id: str | None = Field(default=None, max_length=MAX_IDENTIFIER_CHARS)
    run_id: str | None = Field(default=None, max_length=128)
    task_id: str | None = Field(default=None, max_length=128)
    attempt_id: str | None = Field(default=None, max_length=128)

    # --- Actor -----------------------------------------------------------
    agent_profile: str | None = Field(default=None, max_length=MAX_IDENTIFIER_CHARS)
    adapter: str | None = Field(default=None, max_length=MAX_IDENTIFIER_CHARS)
    model: str | None = Field(default=None, max_length=MAX_IDENTIFIER_CHARS)
    effort: str | None = Field(default=None, max_length=64)

    # --- Git -------------------------------------------------------------
    branch: str | None = Field(default=None, max_length=MAX_BRANCH_CHARS)
    worktree: str | None = Field(default=None, max_length=MAX_PATH_CHARS)
    base_commit: str | None = Field(default=None, max_length=64)
    commit_sha: str | None = Field(default=None, max_length=64)

    # --- Causality and trust ---------------------------------------------
    causal_parent_event_id: str | None = Field(default=None, max_length=64)
    source: Source

    # --- Content ---------------------------------------------------------
    payload: dict[str, Any] = Field(default_factory=dict)

    # --- Integrity, filled by the journal at append time -----------------
    payload_hash: str = ""
    event_hash: str = ""
    prev_event_hash: str = ""
    seq: int | None = None

    # --- Metadata --------------------------------------------------------
    redaction: dict[str, Any] = Field(default_factory=dict)
    integrity: dict[str, Any] = Field(default_factory=dict)

    @field_validator("recorded_at", "occurred_at")
    @classmethod
    def _canonical_timestamp(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _time.normalize(value)

    @field_validator("base_commit", "commit_sha")
    @classmethod
    def _valid_sha(cls, value: str | None) -> str | None:
        """Accept only hexadecimal SHAs.

        A ``commit_sha`` that is not a SHA cannot be resolved against a
        repository, so accepting one would produce a record whose provenance
        looks checkable and is not.
        """
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if not all(c in "0123456789abcdefABCDEF" for c in stripped):
            msg = f"not a hexadecimal commit sha: {value!r}"
            raise ValueError(msg)
        if not 4 <= len(stripped) <= 64:
            msg = f"commit sha has implausible length: {len(stripped)}"
            raise ValueError(msg)
        return stripped.lower()

    @classmethod
    def create(
        cls,
        *,
        event_type: EventType,
        project_id: str,
        source: Source,
        payload: dict[str, Any] | None = None,
        event_id: str | None = None,
        recorded_at: str | None = None,
        **fields: Any,
    ) -> Event:
        """Build an event with identity and timestamps applied.

        Does **not** redact, hash, or scan for poisoning: those happen in the
        admission path (:mod:`provalume.policy.admission`) so that no code path
        can construct a durable event while skipping them.
        """
        return cls(
            event_id=event_id or _ids.new_id(),
            event_type=event_type,
            project_id=project_id,
            source=source,
            payload=payload or {},
            recorded_at=recorded_at or _time.now(),
            **fields,
        )

    @property
    def is_evidence(self) -> bool:
        """Whether this event can contribute to a promotion decision."""
        return self.event_type in EVIDENCE_EVENT_TYPES

    @property
    def is_agent_sourced(self) -> bool:
        """Whether the content originated from an agent and is untrusted."""
        return self.source is Source.AGENT or self.event_type in AGENT_EVENT_TYPES

    def envelope(self) -> dict[str, Any]:
        """The fields that participate in the chain hash."""
        from provalume.interchange.hashing import ENVELOPE_HASH_FIELDS

        return {field: getattr(self, field, None) for field in ENVELOPE_HASH_FIELDS}


class EventFilter(BaseModel):
    """Structured filter for reading events.

    Exists so that no public API needs to accept SQL. Every field becomes a bound
    parameter (threat T23); there is no path from a caller to a query string.
    """

    model_config = ConfigDict(extra="forbid")

    project_id: str | None = None
    event_types: tuple[EventType, ...] = ()
    sources: tuple[Source, ...] = ()
    run_id: str | None = None
    task_id: str | None = None
    attempt_id: str | None = None
    branch: str | None = None
    commit_sha: str | None = None
    since: str | None = None
    until: str | None = None
    limit: int = Field(default=100, ge=1, le=10_000)
    offset: int = Field(default=0, ge=0)
    ascending: bool = True
