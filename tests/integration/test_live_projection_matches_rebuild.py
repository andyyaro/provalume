"""The live write path must project the same numbers as a rebuild.

Found by dogfooding. ``Projector.apply`` built a fresh ``PerformanceAccumulator``
for every event, so the stored aggregate was restated from whichever event
arrived last: an agent that succeeded once in ten attempts was served as
"1/1 succeeded (100%)". Only ``rebuild`` — which shares one accumulator across
the whole journal — computed the real figure, and nothing surfaced the
divergence, because ``audit(deep=True)`` re-hashes what is stored rather than
comparing it against a replay.

The claim under test is ADR-0002's: the journal is the source of truth, so the
projection a live run serves and the projection a rebuild produces are the same
projection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from provalume.schemas.events import EventType
from provalume.schemas.memories import MemoryType
from provalume.schemas.trust import Source

if TYPE_CHECKING:
    from provalume.sdk.client import Provalume

AGENT = "flaky-agent"


def _record_attempts(pv: Provalume, *, ok: int, failed: int) -> None:
    """Record `failed` losing attempts and then `ok` winning ones, live."""
    for index, outcome in enumerate(["failed"] * failed + ["ok"] * ok):
        pv.record_event(
            EventType.ATTEMPT_COMPLETED,
            source=Source.ADAPTER,
            payload={"outcome": outcome, "kind": "implement"},
            agent_profile=AGENT,
            adapter="orkestra",
            model="test-model",
            task_id=f"task-{index}",
            attempt_id=f"attempt-{index}",
        )


def _performance(pv: Provalume) -> dict[str, Any]:
    records = pv.memory_records(memory_types=[MemoryType.PERFORMANCE], limit=10)
    assert records, "no performance aggregate was produced"
    assert len(records) == 1, f"expected one bucket, got {len(records)}"
    return {
        "content": records[0].content,
        "text": records[0].text,
        "hash": records[0].content_hash,
    }


def test_performance_counts_every_live_attempt_not_only_the_last(pv: Provalume) -> None:
    """One success in ten attempts is 1/10, on the path a real run uses."""
    _record_attempts(pv, ok=1, failed=9)

    live = _performance(pv)
    assert live["content"]["attempts"] == 10
    assert live["content"]["successes"] == 1
    assert live["content"]["success_rate"] == 0.1
    assert "1/10 succeeded (10%)" in live["text"], (
        f"the aggregate a live run serves reads {live['text']!r}"
    )


def test_live_projection_is_identical_to_a_rebuild(pv: Provalume) -> None:
    """Same journal, same projection — content hash included."""
    _record_attempts(pv, ok=2, failed=3)
    live = _performance(pv)

    pv.rebuild()
    rebuilt = _performance(pv)

    assert rebuilt["content"] == live["content"]
    assert rebuilt["text"] == live["text"]
    assert rebuilt["hash"] == live["hash"]


def test_re_projecting_a_counted_event_does_not_inflate_the_aggregate(pv: Provalume) -> None:
    """Merging must be idempotent, or a catch-up would double the numbers."""
    _record_attempts(pv, ok=1, failed=1)
    before = _performance(pv)

    for event in pv.journal.iter_all(project_id=pv.project_id):
        pv.projector.apply(event)

    after = _performance(pv)
    assert after["content"]["attempts"] == before["content"]["attempts"] == 2
    assert after["hash"] == before["hash"]


def test_verification_counts_accumulate_across_live_writes(pv: Provalume) -> None:
    """The same defect showed up on verification counts, so pin those too."""
    for index in range(5):
        pv.record_verification(
            command=f"pytest tests/test_{index}.py",
            passed=True,
            purpose="the unit suite",
            agent_profile=AGENT,
            adapter="orkestra",
            task_id=f"task-{index}",
        )

    assert _performance(pv)["content"]["verifications"] == 5

    pv.rebuild()
    assert _performance(pv)["content"]["verifications"] == 5
