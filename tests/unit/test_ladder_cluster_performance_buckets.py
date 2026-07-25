"""What a performance aggregate is allowed to claim.

``verification.passed`` and ``review.approved`` carry no task category, so they
were filed under one named "general" — a bucket with zero attempts, published
next to the agent's real record as a ``verified`` memory reading "<agent>: no
recorded attempts at general.", with ``success_rate`` 0.0 for an agent that had
just succeeded at everything. The same filter dropped reviewer profiles
entirely, since a reviewer produces approvals and nothing else.
"""

from __future__ import annotations

from provalume.schemas.events import Event, EventType
from provalume.schemas.memories import Memory
from provalume.schemas.trust import Source, TrustState
from provalume.writers import runs

OBSERVED = TrustState.OBSERVED
AGENT = "alpha"


def _event(event_type: EventType, **payload: object) -> Event:
    return Event.create(
        event_type=event_type,
        project_id="p",
        source=Source.KERNEL,
        payload=dict(payload),
        agent_profile=AGENT,
        adapter="orkestra",
        model="test-model",
    )


def _accumulate(*events: Event) -> list[Memory]:
    accumulator = runs.PerformanceAccumulator(project_id="p", repository_id="r")
    for event in events:
        accumulator.observe(event)
    return accumulator.build(landing_state=OBSERVED)


def test_no_bucket_claims_an_agent_has_no_track_record() -> None:
    """The phantom bucket contradicted the agent's own record, at `verified`."""
    built = _accumulate(
        *[
            _event(EventType.ATTEMPT_COMPLETED, outcome="ok", task_category="implement")
            for _ in range(5)
        ],
        *[_event(EventType.VERIFICATION_PASSED, command=f"pytest -q {i}") for i in range(5)],
    )

    texts = [memory.text for memory in built]
    assert not any("no recorded attempts" in text for text in texts), texts
    assert any("5/5 succeeded (100%)" in text for text in texts), texts


def test_a_bucket_without_attempts_states_what_it_counted() -> None:
    built = _accumulate(*[_event(EventType.VERIFICATION_PASSED) for _ in range(5)])

    assert len(built) == 1
    assert built[0].text == "alpha: 5 passed verification, and no attempts recorded."


def test_success_rate_is_unknown_rather_than_zero_without_attempts() -> None:
    """0.0 reads as "failed everything" to anything ranking on the field."""
    built = _accumulate(_event(EventType.VERIFICATION_PASSED))

    assert built[0].content["success_rate"] is None
    assert built[0].content["attempts"] == 0


def test_a_reviewer_profile_is_aggregated() -> None:
    """Approvals are the only outcome a reviewer produces; they still count."""
    built = _accumulate(*[_event(EventType.REVIEW_APPROVED, reviewer=AGENT) for _ in range(3)])

    assert len(built) == 1, "review reliability is aggregated for no one"
    assert built[0].content["approvals"] == 3
    assert "3 approved in review" in built[0].text


def test_a_categorised_bucket_still_names_its_category() -> None:
    built = _accumulate(
        _event(EventType.ATTEMPT_COMPLETED, outcome="ok", task_category="migration"),
        _event(EventType.ATTEMPT_COMPLETED, outcome="failed", task_category="migration"),
    )

    assert built[0].text == "alpha on migration: 1/2 succeeded (50%)."
    assert built[0].content["success_rate"] == 0.5


def test_a_bucket_that_counted_nothing_is_not_published() -> None:
    built = _accumulate(_event(EventType.RUN_COMPLETED, outcome="ok"))

    assert built == []
