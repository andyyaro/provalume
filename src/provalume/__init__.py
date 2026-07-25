"""Provalume — verified, git-aware memory for autonomous software agents.

    Facts your agents proved, not things they said.

Provalume records what agents did and which evidence proved it, then re-injects a
bounded, labelled digest of that record into future prompts. Trust is granted by
deterministic evidence — a command that returned, a reviewer who was not the
author, a commit that landed — never by assertion.

    from provalume import Provalume

    pv = Provalume.open(project_id="my-project")

    pv.record_verification(
        command="pytest -n auto tests/integration",
        passed=False,
        excerpt="deadlock in db fixture teardown",
    )

    warning = pv.preflight(command="pytest -n auto tests/integration")
    if warning.matched:
        print(warning.summary)

    digest = pv.recall("integration tests").digest(char_budget=2000)
    print(digest.text)      # always banner-first, always within budget

What this module re-exports is the public API, stable within a minor release
series. Everything else — ``provalume.store``, ``provalume.policy``,
``provalume.writers``, ``provalume.retrieval``, ``provalume.interchange``, and
the ``provalume.mcp`` internals — is internal and may change without notice
(ADR-0017).
"""

from __future__ import annotations

from provalume.errors import (
    BudgetExceeded,
    EmbedderUnavailable,
    IntegrityError,
    InterchangeError,
    PathConfinementError,
    PolicyViolation,
    ProvalumeError,
    SchemaVersionError,
    ScopeError,
    SignatureError,
    StoreError,
    TrustError,
    UnknownRecordVersion,
    ValidationError,
)
from provalume.schemas import (
    DIGEST_BANNER,
    Applicability,
    Digest,
    Event,
    EventFilter,
    EventType,
    Explanation,
    IntegrationState,
    Memory,
    MemoryFilter,
    MemoryType,
    PreflightMatch,
    PreflightResult,
    Provenance,
    RankingPolicy,
    RecallQuery,
    RecallResult,
    ResolutionStatus,
    ReviewState,
    Scope,
    ScopeLevel,
    ScoreBreakdown,
    Source,
    Transition,
    TrustState,
    VerificationState,
)
from provalume.sdk.client import Provalume, RecallResponse

try:  # pragma: no cover - depends on install state, not on logic
    from importlib.metadata import version

    __version__ = version("provalume")
except Exception:  # pragma: no cover
    __version__ = "0.0.0+unknown"

__all__ = [
    "DIGEST_BANNER",
    "Applicability",
    "BudgetExceeded",
    "Digest",
    "EmbedderUnavailable",
    "Event",
    "EventFilter",
    "EventType",
    "Explanation",
    "IntegrationState",
    "IntegrityError",
    "InterchangeError",
    "Memory",
    "MemoryFilter",
    "MemoryType",
    "PathConfinementError",
    "PolicyViolation",
    "PreflightMatch",
    "PreflightResult",
    "Provalume",
    "ProvalumeError",
    "Provenance",
    "RankingPolicy",
    "RecallQuery",
    "RecallResponse",
    "RecallResult",
    "ResolutionStatus",
    "ReviewState",
    "SchemaVersionError",
    "Scope",
    "ScopeError",
    "ScopeLevel",
    "ScoreBreakdown",
    "SignatureError",
    "Source",
    "StoreError",
    "Transition",
    "TrustError",
    "TrustState",
    "UnknownRecordVersion",
    "ValidationError",
    "VerificationState",
    "__version__",
]
