"""Scoring and deterministic ordering (ADR-0008).

The formula, in one place::

    score = w_lex           * lexical
          + w_trust         * trust
          + w_evidence      * evidence
          + w_recency       * recency
          + w_usage         * usage
          + w_type          * type_match
          + w_scope         * scope_specificity
          - p_contradiction * contradiction
          - p_poison        * poisoning_risk

Every component is normalised to ``[0, 1]``; the score itself is **not**
normalised. Its maximum is the sum of positive weights, and scores are comparable
within one query only. A 0-to-1-looking number would invite being read as a
probability, which it is not.

Every component is recorded per result in a :class:`ScoreBreakdown`, so
``provalume explain`` can show the arithmetic. A result that cannot explain
itself is a bug, not a cosmetic gap.
"""

from __future__ import annotations

import math

from provalume import _time
from provalume.schemas.memories import Memory, MemoryType
from provalume.schemas.retrieval import RankingPolicy, ScoreBreakdown
from provalume.schemas.scope import Applicability
from provalume.schemas.trust import evidence_weight, trust_weight


def recency_component(memory: Memory, *, as_of: str | None = None) -> float:
    """Exponential decay with a per-type half-life.

    ``0.5 ** (age / half_life)`` — a record at exactly its half-life scores 0.5.
    Half-lives differ by category because a verified command stays verified for
    far longer than last sprint's episode stays interesting (ADR-0008).
    """
    age = _time.age_days(memory.recorded_at, reference=as_of)
    half_life = memory.half_life_days
    if half_life <= 0:  # pragma: no cover - defensive
        return 1.0
    return float(0.5 ** (age / half_life))


def usage_component(memory: Memory, policy: RankingPolicy) -> float:
    """Log-scaled access count, saturating at ``usage_saturation``.

    Log-scaled so the difference between 1 and 10 accesses matters more than
    between 100 and 110, and weighted weakly (0.15) because usage is
    self-reinforcing: a record that ranks highly gets accessed, which makes it
    rank higher. Left unchecked that ossifies the ranking around whatever was
    retrieved first.
    """
    if memory.access_count <= 0:
        return 0.0
    return min(
        1.0,
        math.log1p(memory.access_count) / math.log1p(policy.usage_saturation),
    )


def type_component(
    memory: Memory,
    requested: tuple[MemoryType, ...],
    policy: RankingPolicy,
) -> float:
    """How well the record's category matches what was asked for.

    A nudge, not a filter. Asking for gotchas should not hide a decisive semantic
    fact that answers the question outright — which is why the mismatch factor is
    0.5 rather than 0.
    """
    if not requested:
        return 1.0
    return 1.0 if memory.memory_type in requested else policy.type_mismatch_factor


def score(
    memory: Memory,
    *,
    policy: RankingPolicy,
    lexical: float,
    scope_specificity: float,
    requested_types: tuple[MemoryType, ...] = (),
    has_contradiction: bool = False,
    as_of: str | None = None,
) -> ScoreBreakdown:
    """Compute the full score and its breakdown."""
    trust = trust_weight(memory.trust_state)
    evidence = evidence_weight(
        memory.verification_state, memory.review_state, memory.integration_state
    )
    recency = recency_component(memory, as_of=as_of)
    usage = usage_component(memory, policy)
    type_match = type_component(memory, requested_types, policy)
    contradiction = 1.0 if has_contradiction else 0.0
    poison = memory.poisoning_risk

    lexical_c = policy.w_lex * lexical
    trust_c = policy.w_trust * trust
    evidence_c = policy.w_evidence * evidence
    recency_c = policy.w_recency * recency
    usage_c = policy.w_usage * usage
    type_c = policy.w_type * type_match
    scope_c = policy.w_scope * scope_specificity
    contradiction_p = policy.p_contradiction * contradiction
    poison_p = policy.p_poison * poison

    total = (
        lexical_c
        + trust_c
        + evidence_c
        + recency_c
        + usage_c
        + type_c
        + scope_c
        - contradiction_p
        - poison_p
    )

    return ScoreBreakdown(
        lexical=round(lexical, 6),
        trust=round(trust, 6),
        evidence=round(evidence, 6),
        recency=round(recency, 6),
        usage=round(usage, 6),
        type_match=round(type_match, 6),
        scope=round(scope_specificity, 6),
        contradiction=contradiction,
        poisoning=round(poison, 6),
        lexical_contribution=round(lexical_c, 6),
        trust_contribution=round(trust_c, 6),
        evidence_contribution=round(evidence_c, 6),
        recency_contribution=round(recency_c, 6),
        usage_contribution=round(usage_c, 6),
        type_contribution=round(type_c, 6),
        scope_contribution=round(scope_c, 6),
        contradiction_penalty=round(contradiction_p, 6),
        poisoning_penalty=round(poison_p, 6),
        total=round(total, 6),
    )


def sort_key(memory: Memory, breakdown: ScoreBreakdown) -> tuple[float, str, str]:
    """Deterministic ordering key: ``(-score, -recorded_at, memory_id)``.

    Full determinism is not a nicety here — the eval harness replays a fixed
    corpus and compares output, so an ordering that varied with dict iteration or
    floating-point tie-breaks would make every measurement noise. Ties break on
    recency, then on identifier, both of which are data rather than accidents of
    execution.

    ``recorded_at`` descends via an inverted lexicographic trick: RFC 3339 sorts
    lexicographically, so the complement of each character reverses it without
    needing a second sort pass.
    """
    inverted_time = "".join(chr(0x10FFFF - ord(c)) for c in memory.recorded_at)
    return (-breakdown.total, inverted_time, memory.memory_id)


def build_reasons(
    memory: Memory,
    *,
    breakdown: ScoreBreakdown,
    applicability: Applicability,
    applicability_reason: str,
    query_branch: str | None,
    has_contradiction: bool,
    resolution_present: bool,
    provenance_summary: str,
) -> tuple[str, ...]:
    """Human-readable reasons this record was returned.

    These are what a person actually reads to decide whether to believe a result,
    so they name evidence rather than restating the score. "verified by
    `pytest -q`" is checkable; "high trust score" is not.
    """
    reasons: list[str] = []

    if breakdown.lexical > 0:
        reasons.append(f"matched the query text (relevance {breakdown.lexical:.2f})")

    reasons.append(f"same project ({memory.scope.project_id})")

    if memory.scope.branch and query_branch and memory.scope.branch == query_branch:
        reasons.append(f"recorded on this branch ({query_branch})")
    elif memory.scope.level.value in {"repository", "project"}:
        reasons.append(f"applies at {memory.scope.level} scope")

    if applicability is Applicability.CURRENT:
        reasons.append(f"current here — {applicability_reason}")
    elif applicability is Applicability.HISTORICAL:
        reasons.append(f"historical — {applicability_reason}")
    elif applicability is Applicability.CROSS_SCOPE:
        reasons.append(f"from another scope — {applicability_reason}")
    else:
        reasons.append(f"applicability uncertain — {applicability_reason}")

    if provenance_summary:
        reasons.append(provenance_summary)

    if memory.access_count > 0:
        times = "once" if memory.access_count == 1 else f"{memory.access_count} times"
        reasons.append(f"retrieved {times} before")

    if resolution_present:
        reasons.append("linked to what later worked")

    if has_contradiction:
        reasons.append("contested by another current record on the same subject")

    if memory.poisoning_risk > 0:
        reasons.append(
            f"carries a poisoning-risk penalty of {memory.poisoning_risk:.2f}"
        )

    return tuple(reasons)


def build_warnings(
    memory: Memory,
    *,
    applicability: Applicability,
    has_contradiction: bool,
    provenance_resolved: bool,
) -> tuple[str, ...]:
    """Per-result caveats.

    Warnings state what a reader must not conclude. They are separate from
    reasons because a reason explains a match, while a warning constrains how far
    the result may be trusted — and the second must not be lost in the first.
    """
    warnings: list[str] = []

    if not memory.presentable_as_current_truth:
        if memory.memory_type is MemoryType.SEMANTIC:
            warnings.append(
                "NOT established project truth: this semantic record has not landed "
                "in history, so it reflects one branch or one attempt"
            )
        elif memory.trust_state.value in {"invalidated", "superseded", "rejected"}:
            warnings.append(
                f"withdrawn ({memory.trust_state}); retained as history, not as fact"
            )
        else:
            warnings.append(f"not established truth (trust state {memory.trust_state})")

    if applicability is Applicability.UNCERTAIN:
        warnings.append("applicability at the queried commit could not be determined")
    elif applicability is Applicability.HISTORICAL:
        warnings.append("introduced on a different line of history")
    elif applicability is Applicability.CROSS_SCOPE:
        warnings.append("recorded in a different scope than the query")

    if has_contradiction:
        warnings.append("contradicted by another current record; neither is auto-resolved")

    if not provenance_resolved:
        warnings.append("provenance could not be fully resolved")

    if memory.poisoning_risk > 0:
        warnings.append(
            f"matched {len(memory.poisoning_matches)} poisoning heuristic(s); "
            "treat the content with extra suspicion"
        )

    if memory.trust_state.value == "quarantined":
        warnings.append("QUARANTINED: untrusted content, shown only because requested")

    return tuple(warnings)
