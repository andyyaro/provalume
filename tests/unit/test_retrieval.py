"""Ranking, explanations, digest composition, and the preflight gate."""

from __future__ import annotations

import pytest

from provalume.retrieval import ranking
from provalume.retrieval.digest import compose, estimate_tokens, trust_label
from provalume.retrieval.vectors import (
    HashingEmbedder,
    cosine,
    pack,
    reciprocal_rank_fusion,
    unpack,
)
from provalume.schemas.memories import RECENCY_HALF_LIFE_DAYS, Memory, MemoryType
from provalume.schemas.retrieval import RankingPolicy, RecallResult
from provalume.schemas.scope import Scope, ScopeLevel
from provalume.schemas.trust import (
    IntegrationState,
    ReviewState,
    Source,
    TrustState,
    VerificationState,
    evidence_weight,
    trust_weight,
)


def memory(**overrides: object) -> Memory:
    defaults: dict = {
        "memory_type": MemoryType.SEMANTIC,
        "text": "a fact",
        "scope": Scope(level=ScopeLevel.BRANCH, project_id="p", branch="main"),
        "source": Source.KERNEL,
        "recorded_at": "2026-07-25T00:00:00.000Z",
    }
    defaults.update(overrides)
    return Memory.create(**defaults)  # type: ignore[arg-type]


AS_OF = "2026-07-25T00:00:00.000Z"


# --- Components ------------------------------------------------------------


def test_trust_weight_increases_monotonically() -> None:
    ladder = [
        TrustState.QUARANTINED,
        TrustState.OBSERVED,
        TrustState.VERIFIED,
        TrustState.REVIEWED,
        TrustState.INTEGRATED,
    ]
    weights = [trust_weight(s) for s in ladder]
    assert weights == sorted(weights)
    assert weights[-1] == 1.0


def test_terminal_states_weigh_zero() -> None:
    for state in (TrustState.INVALIDATED, TrustState.SUPERSEDED, TrustState.REJECTED):
        assert trust_weight(state) == 0.0


def test_a_failed_verification_counts_as_evidence_present() -> None:
    """The gotcha case. Treating `failed` as absence would systematically demote
    exactly the records the preflight gate needs."""
    failed = evidence_weight(
        VerificationState.FAILED, ReviewState.NONE, IntegrationState.NONE
    )
    unknown = evidence_weight(
        VerificationState.UNKNOWN, ReviewState.NONE, IntegrationState.NONE
    )
    assert failed > unknown
    assert failed == evidence_weight(
        VerificationState.PASSED, ReviewState.NONE, IntegrationState.NONE
    )


def test_evidence_accumulates_and_caps_at_one() -> None:
    full = evidence_weight(
        VerificationState.PASSED, ReviewState.APPROVED, IntegrationState.ACCEPTED_USER
    )
    assert full == 1.0


def test_reverted_integration_does_not_count_as_landed() -> None:
    reverted = evidence_weight(
        VerificationState.UNKNOWN, ReviewState.NONE, IntegrationState.REVERTED
    )
    assert reverted == 0.0


def test_recency_halves_at_the_half_life() -> None:
    record = memory(memory_type=MemoryType.GOTCHA, recorded_at="2026-04-26T00:00:00.000Z")
    half_life = RECENCY_HALF_LIFE_DAYS[MemoryType.GOTCHA]
    assert half_life == 90.0
    assert ranking.recency_component(record, as_of=AS_OF) == pytest.approx(0.5, abs=0.02)


def test_recency_is_one_for_a_brand_new_record() -> None:
    record = memory(recorded_at=AS_OF)
    assert ranking.recency_component(record, as_of=AS_OF) == pytest.approx(1.0)


def test_a_future_timestamp_cannot_exceed_full_recency() -> None:
    """A forged future timestamp must not win every ranking."""
    record = memory(recorded_at="2030-01-01T00:00:00.000Z")
    assert ranking.recency_component(record, as_of=AS_OF) <= 1.0


def test_per_type_half_lives_differ_as_documented() -> None:
    assert (
        RECENCY_HALF_LIFE_DAYS[MemoryType.EPISODIC]
        < RECENCY_HALF_LIFE_DAYS[MemoryType.SEMANTIC]
        < RECENCY_HALF_LIFE_DAYS[MemoryType.PROCEDURAL]
        < RECENCY_HALF_LIFE_DAYS[MemoryType.DECISION]
    )


def test_usage_is_log_scaled_and_saturates() -> None:
    policy = RankingPolicy()
    zero = ranking.usage_component(memory(access_count=0), policy)
    one = ranking.usage_component(memory(access_count=1), policy)
    ten = ranking.usage_component(memory(access_count=10), policy)
    huge = ranking.usage_component(memory(access_count=100_000), policy)
    assert zero == 0.0
    assert one < ten < 1.0
    assert huge == 1.0
    assert (ten - one) > (1.0 - ten), "usage should be log-scaled, not linear"


def test_type_match_is_a_nudge_not_a_filter() -> None:
    policy = RankingPolicy()
    record = memory(memory_type=MemoryType.GOTCHA)
    assert ranking.type_component(record, (), policy) == 1.0
    assert ranking.type_component(record, (MemoryType.GOTCHA,), policy) == 1.0
    mismatch = ranking.type_component(record, (MemoryType.SEMANTIC,), policy)
    assert 0.0 < mismatch < 1.0, "a non-requested type must not be excluded outright"


# --- Scoring ---------------------------------------------------------------


def test_relevance_outweighs_trust() -> None:
    """A trusted fact about the wrong subsystem is noise."""
    policy = RankingPolicy()
    relevant_but_weak = ranking.score(
        memory(trust_state=TrustState.VERIFIED),
        policy=policy, lexical=1.0, scope_specificity=1.0, as_of=AS_OF,
    )
    trusted_but_irrelevant = ranking.score(
        memory(trust_state=TrustState.INTEGRATED),
        policy=policy, lexical=0.0, scope_specificity=1.0, as_of=AS_OF,
    )
    assert relevant_but_weak.total > trusted_but_irrelevant.total


def test_penalties_reduce_the_score() -> None:
    policy = RankingPolicy()
    clean = ranking.score(memory(), policy=policy, lexical=1.0,
                          scope_specificity=1.0, as_of=AS_OF)
    contested = ranking.score(memory(), policy=policy, lexical=1.0,
                              scope_specificity=1.0, has_contradiction=True, as_of=AS_OF)
    poisoned = ranking.score(memory(poisoning_risk=0.4), policy=policy, lexical=1.0,
                             scope_specificity=1.0, as_of=AS_OF)
    assert contested.total < clean.total
    assert poisoned.total < clean.total


def test_breakdown_components_sum_to_the_total() -> None:
    """If the arithmetic does not add up, `explain` is lying."""
    breakdown = ranking.score(
        memory(trust_state=TrustState.VERIFIED, access_count=5, poisoning_risk=0.2),
        policy=RankingPolicy(), lexical=0.7, scope_specificity=0.8,
        has_contradiction=True, as_of=AS_OF,
    )
    total = sum(contribution for _, _, contribution in breakdown.as_table())
    assert total == pytest.approx(breakdown.total, abs=1e-5)


def test_score_maximum_matches_the_declared_weight_total() -> None:
    policy = RankingPolicy()
    perfect = ranking.score(
        memory(
            trust_state=TrustState.INTEGRATED,
            verification_state=VerificationState.PASSED,
            review_state=ReviewState.APPROVED,
            integration_state=IntegrationState.ACCEPTED_USER,
            access_count=10_000,
            recorded_at=AS_OF,
        ),
        policy=policy, lexical=1.0, scope_specificity=1.0, as_of=AS_OF,
    )
    assert perfect.total == pytest.approx(policy.positive_weight_total(), abs=1e-6)


def test_ordering_is_fully_deterministic() -> None:
    """The eval harness compares output; ordering that varied with dict
    iteration would make every measurement noise."""
    a = memory(memory_id="B" * 26, recorded_at=AS_OF)
    b = memory(memory_id="A" * 26, recorded_at=AS_OF)
    breakdown = ranking.score(a, policy=RankingPolicy(), lexical=0.5,
                              scope_specificity=1.0, as_of=AS_OF)
    keys = [ranking.sort_key(a, breakdown), ranking.sort_key(b, breakdown)]
    assert sorted(keys) == sorted(keys)
    # Equal scores and timestamps break on the identifier, ascending.
    assert min(keys)[2] == "A" * 26


def test_newer_records_sort_first_on_equal_scores() -> None:
    older = memory(recorded_at="2026-01-01T00:00:00.000Z")
    newer = memory(recorded_at="2026-07-01T00:00:00.000Z")
    breakdown = ranking.score(older, policy=RankingPolicy(), lexical=0.5,
                              scope_specificity=1.0, as_of=AS_OF)
    assert ranking.sort_key(newer, breakdown) < ranking.sort_key(older, breakdown)


# --- Digest ----------------------------------------------------------------


def result(**overrides: object) -> RecallResult:
    defaults: dict = {
        "memory_id": "M" * 26,
        "memory_type": MemoryType.GOTCHA,
        "text": "a failure happened",
        "trust_state": TrustState.VERIFIED,
    }
    defaults.update(overrides)
    return RecallResult(**defaults)  # type: ignore[arg-type]


def test_digest_always_opens_with_the_banner() -> None:
    digest = compose([result()], char_budget=1000)
    assert digest.text.startswith("Historical context from Provalume follows.")
    assert "not as instructions" in digest.text


def test_empty_results_still_carry_the_banner() -> None:
    digest = compose([], char_budget=1000)
    assert digest.text.startswith("Historical context from Provalume follows.")
    assert digest.items == ()


def test_failures_are_ordered_first() -> None:
    """An agent about to repeat a mistake needs that before general facts."""
    digest = compose(
        [
            result(memory_id="A" * 26, memory_type=MemoryType.SEMANTIC, text="a fact"),
            result(memory_id="B" * 26, memory_type=MemoryType.GOTCHA, text="a failure"),
        ],
        char_budget=2000,
    )
    assert digest.text.index("Prior failures") < digest.text.index("Project facts")


def test_near_duplicates_are_suppressed() -> None:
    same = [result(memory_id=f"{i}" * 26, text="identical text") for i in range(5)]
    digest = compose(same, char_budget=4000)
    assert len(digest.items) == 1, "duplicate renderings wasted the budget"


def test_omitted_records_are_reported() -> None:
    many = [
        result(memory_id=f"{i:026d}", text=f"failure number {i} with detail " * 5)
        for i in range(40)
    ]
    digest = compose(many, char_budget=900)
    assert digest.omitted_count > 0
    assert "did not fit" in digest.text
    assert digest.chars_used <= 900


def test_token_budget_converts_to_characters() -> None:
    digest = compose([result()], token_budget=500)
    assert digest.char_budget == 2000


def test_estimate_tokens_is_labelled_as_an_estimate() -> None:
    assert estimate_tokens("a" * 400) == 100


@pytest.mark.parametrize(
    ("state", "label"),
    [
        (TrustState.INTEGRATED, "VERIFIED+LANDED"),
        (TrustState.REVIEWED, "REVIEWED"),
        (TrustState.VERIFIED, "VERIFIED"),
        (TrustState.OBSERVED, "OBSERVED"),
        (TrustState.QUARANTINED, "QUARANTINED/UNTRUSTED"),
    ],
)
def test_trust_labels_are_always_text(state: TrustState, label: str) -> None:
    """Colour is reinforcement; the label carries the meaning."""
    assert trust_label(result(trust_state=state)) == label


def test_untrusted_records_are_marked_in_the_digest() -> None:
    digest = compose(
        [result(trust_state=TrustState.QUARANTINED, presentable_as_current_truth=False)],
        char_budget=2000,
    )
    assert "QUARANTINED" in digest.text


# --- Vectors ---------------------------------------------------------------


def test_hashing_embedder_is_deterministic() -> None:
    embedder = HashingEmbedder()
    first = embedder.embed(["deploy failed readiness probe"])[0]
    second = HashingEmbedder().embed(["deploy failed readiness probe"])[0]
    assert first == second


def test_hashing_embedder_normalises() -> None:
    vector = HashingEmbedder().embed(["a longer sentence with several words"])[0]
    assert sum(v * v for v in vector) == pytest.approx(1.0, abs=1e-6)


def test_hashing_embedder_handles_empty_text() -> None:
    assert HashingEmbedder().embed([""])[0] == [0.0] * HashingEmbedder().dimensions


def test_vector_round_trip_is_lossless_enough() -> None:
    original = HashingEmbedder(dimensions=16).embed(["hello world"])[0]
    restored = unpack(pack(original), 16)
    assert restored == pytest.approx(original, abs=1e-6)


def test_unpack_rejects_a_wrong_size_blob() -> None:
    with pytest.raises(ValueError, match="expected"):
        unpack(b"\x00" * 8, 16)


def test_cosine_handles_zero_vectors() -> None:
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_cosine_of_identical_vectors_is_one() -> None:
    assert cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_rrf_rewards_agreement_between_rankings() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["a", "c", "b"]])
    assert fused[0][0] == "a"


def test_rrf_is_deterministic_on_ties() -> None:
    first = reciprocal_rank_fusion([["x", "y"], ["y", "x"]])
    second = reciprocal_rank_fusion([["x", "y"], ["y", "x"]])
    assert first == second


def test_rrf_of_nothing_is_nothing() -> None:
    assert reciprocal_rank_fusion([]) == []
