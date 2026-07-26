"""Contracts the retrieval surface publishes, pinned against the code.

Each test here exists because the documented behaviour and the shipped behaviour
had drifted apart: a warning tier that fired on a substring, a digest that
trimmed its own footer, a count that blamed the budget for a deduplication, a
flag that reads as available, and two documentation samples that no run could
produce. Where a claim is made in prose, the test reads the prose.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from provalume.retrieval.digest import compose
from provalume.retrieval.preflight import PreflightGate
from provalume.schemas.memories import MemoryType
from provalume.schemas.retrieval import (
    DIGEST_BANNER,
    Explanation,
    RecallQuery,
    RecallResult,
)
from provalume.schemas.scope import Applicability
from provalume.schemas.trust import TrustState
from provalume.sdk.client import Provalume

_REPO_ROOT = Path(__file__).resolve().parents[2]
_README = _REPO_ROOT / "README.md"
_RETRIEVAL_DOC = _REPO_ROOT / "docs" / "reference" / "RETRIEVAL.md"

_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T[\d:.]+Z")


@pytest.fixture
def seeded_lexical(pv: Provalume) -> Provalume:
    pv.record_verification(
        command="pytest -n auto tests/integration",
        passed=False,
        excerpt="E TimeoutError: deadlock in db fixture",
        error_kind="test_failure",
        purpose="the integration suite",
    )
    pv.record_fact(subject="package manager", statement="The project uses uv.")
    return pv


# --- The pre-action gate ---------------------------------------------------


def test_a_short_subsystem_does_not_match_a_word_that_contains_it(
    pv: Provalume,
) -> None:
    """`subsystem="ui"` must not warn about `npm run build`.

    The reason this tier emits — "previously failed in ui" — is a claim with a
    record attached. Matching on a raw substring makes that claim about any
    command whose text happens to contain the letters.
    """
    pv.record_verification(
        command="npm run build", passed=False, excerpt="E ENOENT", error_kind="e"
    )
    pv.record_verification(
        command="cargo test --all-features", passed=False, excerpt="E panicked", error_kind="e"
    )

    for subsystem in ("ui", "go", "cc", "arg"):
        result = pv.preflight(subsystem=subsystem, record=False)
        assert not result.matched, (
            f"subsystem {subsystem!r} matched on a substring: "
            f"{[r for m in result.matches for r in m.match_reasons]}"
        )

    # A whole-word subsystem still matches, including one that appears inside a
    # path-shaped token.
    assert pv.preflight(subsystem="build", record=False).matched
    assert pv.preflight(subsystem="cargo", record=False).matched


def test_a_path_shaped_command_still_matches_its_subsystem(pv: Provalume) -> None:
    """Whole-word matching must split `tests/integration`, not keep it whole."""
    pv.record_verification(
        command="pytest -n auto tests/integration",
        passed=False,
        excerpt="E deadlock",
        error_kind="e",
    )
    assert pv.preflight(subsystem="integration", record=False).matched


def test_a_command_only_check_warns_and_never_blocks(pv: Provalume) -> None:
    """Blocking needs the error, not just the command (module docstring).

    A caller that has not run anything cannot produce an exact failure
    signature, so it reaches 0.85 and warns. The same caller supplying the error
    it observed reaches 1.00 and, under an explicit policy, blocks.
    """
    excerpt = "E TimeoutError: deadlock"
    for _ in range(2):
        pv.record_verification(
            command="flaky-cmd", passed=False, excerpt=excerpt, error_kind="test_failure"
        )

    strict = PreflightGate(pv.memories, allow_blocking=True)

    pre_action = strict.check(project_id=pv.project_id, command="flaky-cmd")
    assert pre_action.matched
    assert not pre_action.should_block, "a command-only check must not block"
    assert pre_action.matches[0].confidence == 0.85
    assert pre_action.matches[0].match_reasons == ("same command failed previously",)
    assert "warning, not a block" in pre_action.summary

    retry = strict.check(
        project_id=pv.project_id, command="flaky-cmd", error_kind="test_failure", error_text=excerpt
    )
    assert retry.should_block, "a caller supplying the observed error should reach tier 1"


# --- The digest ------------------------------------------------------------


def _digest_result(index: int, warnings: tuple[str, ...]) -> RecallResult:
    return RecallResult(
        memory_id=f"M{index:03d}",
        memory_type=MemoryType.GOTCHA,
        text=f"record {index}: a failure worth several dozen characters of text",
        trust_state=TrustState.VERIFIED,
        explanation=Explanation(
            warnings=warnings,
            applicability=Applicability.UNCERTAIN,
        ),
    )


def test_the_footer_is_never_cut_mid_sentence() -> None:
    """The budget is enforced by construction, footer included.

    The reserve has to cover the footer that will actually be rendered. A fixed
    reserve smaller than the real footer means the omission notice and the
    warnings line get sliced, which is a post-hoc trim by another name.
    """
    warnings = (
        "applicability at the queried commit could not be determined",
        "contradicted by another current record; neither is auto-resolved",
    )
    results = [_digest_result(i, warnings) for i in range(12)]

    minimum = len(DIGEST_BANNER) + 1 + 160
    for budget in range(minimum, minimum + 900):
        digest = compose(results, char_budget=budget)
        assert digest.chars_used <= budget, f"budget {budget} overrun"
        if "[warnings:" in digest.text:
            assert digest.text.endswith("]"), f"warnings line cut at budget {budget}"
            for message in digest.warnings:
                assert message in digest.text, f"{message!r} cut at budget {budget}"
        if digest.omitted_count:
            assert "did not fit the" in digest.text
            assert f"{budget}-character budget.]" in digest.text


def test_near_duplicates_are_not_reported_as_budget_overflow(pv: Provalume) -> None:
    """`omitted_count` means the budget bound. Raising it must change the number.

    A record suppressed because another reads identically did not fail to fit,
    and telling the caller it did sends them to a setting that cannot help.
    """
    for _ in range(2):
        pv.record_verification(command="pytest -q", passed=False, excerpt="E boom", error_kind="e")

    response = pv.recall("pytest boom", limit=20)
    digest = response.digest(char_budget=100_000)

    assert len(digest.items) < len(response.results), "no duplicate was suppressed"
    assert digest.omitted_count == 0, "the budget was nowhere near binding"
    assert digest.suppressed_duplicates >= 1
    assert "did not fit" not in digest.text


# --- Provenance on the read path -------------------------------------------


def test_a_record_citing_a_missing_event_is_flagged_on_recall(pv: Provalume) -> None:
    """Threat T15 on the read path, not only in `explain`.

    A record whose evidence is absent from the journal is served with a caveat
    attached at the point of reading, and counted in the digest rollup.
    """
    pv.record_verification(command="pytest -q", passed=True, purpose="the suite")
    memory = pv.memory_records(memory_types=[MemoryType.PROCEDURAL])[0]
    with pv.db.tx() as conn:
        conn.execute(
            "UPDATE memories SET source_event_ids = ? WHERE memory_id = ?",
            ('["GHOST-EVENT-ID"]', memory.memory_id),
        )

    response = pv.recall("pytest", limit=10)
    flagged = [r for r in response.results if r.memory_id == memory.memory_id]
    assert flagged, "the record was not returned at all"
    assert "provenance could not be fully resolved" in flagged[0].explanation.warnings

    digest = response.digest(char_budget=4_000)
    assert "provenance unresolved for some records" in digest.warnings
    assert "provenance could not be fully resolved" in digest.text


def test_intact_provenance_carries_no_caveat(pv: Provalume) -> None:
    pv.record_verification(command="pytest -q", passed=True, purpose="the suite")
    for result in pv.recall("pytest", limit=10).results:
        assert not any("provenance" in w for w in result.explanation.warnings)


# --- Query parameters mean one thing ---------------------------------------


def test_type_is_a_nudge_on_the_browse_path_too(pv: Provalume) -> None:
    """No query text is a browse, not a different contract for `memory_types`."""
    pv.record_verification(command="deploy.sh", passed=False, excerpt="E rollback", error_kind="e")
    pv.record_fact(subject="deploy", statement="Deploys run from the release branch.")

    browsed = pv.recall("", memory_types=[MemoryType.GOTCHA], limit=20).results
    kinds = {r.memory_type for r in browsed}
    assert MemoryType.GOTCHA in kinds, "the requested type was not returned"
    assert kinds - {MemoryType.GOTCHA}, (
        "no other type survived a browse — memory_types became a hard filter"
    )
    for result in browsed:
        expected = 1.0 if result.memory_type is MemoryType.GOTCHA else 0.5
        assert result.explanation.breakdown.type_match == expected


def test_as_of_moves_recency_and_not_validity(pv: Provalume) -> None:
    """The documented scope of `as_of`, pinned so the docstring stays honest."""
    pv.record_fact(subject="ci", statement="CI runs on CircleCI.")
    pv.record_fact(subject="ci", statement="CI runs on GitHub Actions.", changed=True)

    engine = pv.engine
    past = engine.recall(
        RecallQuery(
            project_id=pv.project_id, query="CircleCI", limit=10, as_of="2000-01-01T00:00:00.000Z"
        )
    )
    assert not past, "as_of resurrected a withdrawn record; the docstring says it cannot"

    withdrawn = engine.recall(
        RecallQuery(project_id=pv.project_id, query="CircleCI", limit=10, include_terminal=True)
    )
    assert withdrawn, "include_terminal is the documented way to see it"


def test_use_vectors_is_accepted_and_inert(seeded_lexical: Provalume) -> None:
    """Documented as reserved and not yet honoured. Pinned so it stays honest.

    If this test starts failing because vectors were wired up, the docstring on
    `RecallQuery.use_vectors`, RETRIEVAL.md §Optional vectors and the README
    bullet all have to change with it.
    """
    engine = seeded_lexical.engine
    base = {"project_id": seeded_lexical.project_id, "query": "integration", "limit": 10}
    without = engine.recall(RecallQuery(**base))
    with_flag = engine.recall(RecallQuery(**base, use_vectors=True))

    assert [r.memory_id for r in with_flag] == [r.memory_id for r in without]
    assert seeded_lexical.db.scalar("SELECT COUNT(*) FROM memory_vectors") == 0


# --- Documentation samples -------------------------------------------------


def test_the_readme_preflight_sample_is_what_the_gate_prints(pv: Provalume) -> None:
    """The headline sample, diffed against a live run of the command it shows."""
    for _ in range(2):
        pv.record_verification(
            command="pytest -n auto tests/integration",
            passed=False,
            excerpt="exit 1 - deadlock in db fixture teardown",
            error_kind="test_failure",
            purpose="the integration suite",
            task_id="t1",
            attempt_id="attempt-1",
            agent_profile="agent-A",
        )
    pv.record_verification(
        command="pytest -p no:xdist tests/integration",
        passed=True,
        purpose="the integration suite",
        task_id="t1",
        attempt_id="attempt-2",
        agent_profile="agent-A",
    )

    summary = pv.preflight(command="pytest -n auto tests/integration").summary
    live = [_TIMESTAMP.sub("<TS>", line) for line in summary.splitlines() if line.strip()]

    text = _README.read_text(encoding="utf-8")
    start = text.index('$ provalume preflight --command "pytest -n auto tests/integration"')
    block = text[start : text.index("```", start)].splitlines()[1:]
    documented = [_TIMESTAMP.sub("<TS>", line) for line in block if line.strip()]

    assert documented == live


def test_the_readme_does_not_pin_a_stale_version_or_claim(pv: Provalume) -> None:
    text = _README.read_text(encoding="utf-8")
    assert "0.1.0" not in text, "the README pins a version the project has moved past"
    assert "has not been dogfooded on production runs yet" not in text
    assert "LIMITATIONS.md) §1" in text, "the bullet should point at the updated section"


def test_the_documented_score_breakdown_adds_up() -> None:
    """RETRIEVAL.md opens by promising every constant is there and correct."""
    text = _RETRIEVAL_DOC.read_text(encoding="utf-8")
    block = text[
        text.index("lexical         1.000") : text.index(
            "TOTAL", text.index("lexical         1.000")
        )
        + 40
    ]
    contributions = [float(v) for v in re.findall(r"x weight = ([+-][\d.]+)", block)]
    total = float(re.search(r"TOTAL\s+([\d.]+)", block).group(1))  # type: ignore[union-attr]
    assert contributions, "the worked example lost its component rows"
    assert round(sum(contributions), 3) == total
