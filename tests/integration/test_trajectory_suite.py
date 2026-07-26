"""The trajectory suite, tested the way this project has learned to test.

The committed fixtures must pass, and a reintroduced defect must visibly
fail — a suite that cannot fail is the failure mode this project keeps paying
for. Mutation tests below cover both sides: expectation-side (a wrong or
missing expectation) and engine-side (a regression in what the engine records,
retrieves, or renders). The suite-level counters are pinned to their exact
values so that eroding a check, padding a denominator, or relabelling a
regression as a known limitation cannot pass silently; changing the fixtures
means updating the pins here, deliberately.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from provalume.evals import trajectories
from provalume.evals.metrics import Metrics
from provalume.evals.trajectories import (
    TrajectoryFixture,
    _run,
    discover,
    load_fixture,
    run_all,
    run_one,
)

FIXTURES = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "trajectories"
COMMITTED_NAMES = ["env-gotcha", "fix-lands", "repeat-blocked", "two-command-gate"]
RESULTS = FIXTURES.parents[1] / "results" / "trajectories" / "results.json"


@pytest.fixture(scope="module")
def suite_results() -> trajectories.TrajectoryResults:
    return run_all(FIXTURES)


def test_committed_fixtures_all_pass(suite_results: trajectories.TrajectoryResults) -> None:
    failures = [(t["name"], t["failures"]) for t in suite_results.trajectories if not t["passed"]]
    assert suite_results.passed, failures
    # The explicit list, not a directory count: adding a fixture must be a
    # deliberate act that also updates the counter pins below.
    assert [p.name for p in discover(FIXTURES)] == COMMITTED_NAMES
    assert [t["name"] for t in suite_results.trajectories] == COMMITTED_NAMES


def test_suite_measures_exactly_what_the_fixtures_pin(
    suite_results: trajectories.TrajectoryResults,
) -> None:
    """Exact counter values, not floors.

    Slack between a floor and the real count is room to erode checks without
    any guard noticing; these pins make every fixture change show up here.
    """
    m = suite_results.metrics
    assert (m.repeated_error.numerator, m.repeated_error.denominator) == (0, 16)
    assert (m.false_warnings.numerator, m.false_warnings.denominator) == (0, 9)
    assert (m.occurrence_fidelity.numerator, m.occurrence_fidelity.denominator) == (16, 16)
    assert (m.resolution_surfacing.numerator, m.resolution_surfacing.denominator) == (6, 6)
    assert (m.digest_inclusion.numerator, m.digest_inclusion.denominator) == (15, 15)
    assert (m.recall_coverage.numerator, m.recall_coverage.denominator) == (4, 4)
    assert (m.recall_precision.numerator, m.recall_precision.denominator) == (4, 4)
    assert m.retrieval_latency.samples
    assert m.write_latency.samples
    # Known-limitation probes are counted so a new escape hatch is visible.
    known = sum(
        1
        for name in COMMITTED_NAMES
        for probe in load_fixture(FIXTURES / name).expectations.get("probes", ())
        if "known_limitation" in probe
    )
    assert known == 3
    # The agent-outcome metrics stay visibly unrun: replay cannot honestly
    # populate them (LIMITATIONS §1), and a nonzero denominator here means
    # something started pretending it can.
    assert m.task_completion.denominator == 0
    assert m.verification_improvement.denominator == 0
    assert m.review_cycle_reduction.denominator == 0


def test_committed_results_match_a_fresh_run(
    suite_results: trajectories.TrajectoryResults,
) -> None:
    """The published results file must be what the suite actually produces.

    Latency and context statistics are machine-dependent and excluded; every
    counter and every pass flag is not.
    """
    committed = json.loads(RESULTS.read_text())
    fresh = suite_results.as_dict()
    assert [(t["name"], t["passed"]) for t in committed["trajectories"]] == [
        (t["name"], t["passed"]) for t in fresh["trajectories"]
    ]
    for name, value in committed["metrics"].items():
        if isinstance(value, dict) and "denominator" in value:
            assert fresh["metrics"][name] == value, name


# --- expectation-side mutations ---------------------------------------------


def _mutated(fixture: TrajectoryFixture, **overrides: object) -> TrajectoryFixture:
    expectations = copy.deepcopy(fixture.expectations)
    for key, value in overrides.items():
        expectations[key] = value
    return TrajectoryFixture(fixture.name, fixture.calls, expectations)


def test_wrong_occurrence_count_fails() -> None:
    """Mutation: mis-state ground truth occurrences and the suite must say so."""
    fixture = load_fixture(FIXTURES / "fix-lands")
    checkpoints = copy.deepcopy(fixture.expectations["checkpoints"])
    target = next(c for c in checkpoints if c["expect"].get("occurrences") == 2)
    target["expect"]["occurrences"] = 7
    mutated = _mutated(fixture, checkpoints=checkpoints)
    result = _run(mutated, 1, Metrics())
    assert not result.passed
    assert any("occurrences" in f and "expected 7" in f for f in result.failures)


def test_false_warning_would_be_caught() -> None:
    """Mutation: expect silence where the gate warns; the suite must fail."""
    fixture = load_fixture(FIXTURES / "fix-lands")
    checkpoints = copy.deepcopy(fixture.expectations["checkpoints"])
    target = next(
        c
        for c in checkpoints
        if c["expect"].get("matched") is True and "occurrences" in c["expect"]
    )
    target["expect"] = {"matched": False}
    mutated = _mutated(fixture, checkpoints=checkpoints)
    metrics = Metrics()
    result = _run(mutated, 1, metrics)
    assert not result.passed
    assert metrics.false_warnings.numerator >= 1
    assert any("warned with no prior failure" in f for f in result.failures)


def test_missed_resolution_would_be_caught() -> None:
    """Mutation: point the resolution at the wrong landing; must fail."""
    fixture = load_fixture(FIXTURES / "fix-lands")
    checkpoints = copy.deepcopy(fixture.expectations["checkpoints"])
    for checkpoint in checkpoints:
        if "resolution_names_commit_of_call" in checkpoint["expect"]:
            # Call 25 in two-command-gate / another landing here would be
            # self-consistent; a *different* landing's sha cannot match.
            checkpoint["expect"]["resolution_names_commit_of_call"] = 25
    mutated = _mutated(fixture, checkpoints=checkpoints)
    result = _run(mutated, 1, Metrics())
    assert not result.passed
    assert any("resolution should name landing" in f for f in result.failures)


def test_dropped_warning_checkpoint_breaks_the_journal_count() -> None:
    """Mutation: quietly delete a warning-bearing checkpoint; the aggregate
    warning.shown equality must name the discrepancy."""
    fixture = load_fixture(FIXTURES / "fix-lands")
    checkpoints = [c for c in copy.deepcopy(fixture.expectations["checkpoints"]) if c["call"] != 13]
    mutated = _mutated(fixture, checkpoints=checkpoints)
    result = _run(mutated, 1, Metrics())
    assert not result.passed
    assert any("warning.shown count 4, expected 3" in f for f in result.failures)


# --- engine-side mutations ---------------------------------------------------


def test_silent_write_loss_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: swallow verification writes and the journal check must fail.

    This is the §4 discipline made executable — asserting a write *happened*,
    not that nothing raised.
    """
    from provalume.sdk.client import Provalume

    def swallowed(self: Provalume, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(Provalume, "record_verification", swallowed)
    fixture = load_fixture(FIXTURES / "repeat-blocked")
    result = _run(fixture, 1, Metrics())
    assert not result.passed
    assert any("verification.failed" in f and "journal" in f for f in result.failures)


def test_hollowed_digest_content_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: keep every digest heading and item count, gut the record
    bodies. Heading-only expectations would stay green; content expectations
    must not."""
    from provalume.integrations.orkestra import OrkestraAdapter

    original = OrkestraAdapter.brief_digest

    def hollowed(self: OrkestraAdapter, **kwargs: object) -> object:
        digest = original(self, **kwargs)
        lines = [
            "- [VERIFIED] lorem ipsum, no recorded knowledge here."
            if line.startswith("- ")
            else line
            for line in digest.text.splitlines()
        ]
        return digest.model_copy(update={"text": "\n".join(lines)})

    monkeypatch.setattr(OrkestraAdapter, "brief_digest", hollowed)
    metrics = Metrics()
    result = _run(load_fixture(FIXTURES / "fix-lands"), 1, metrics)
    assert not result.passed
    assert any("digest missing" in f for f in result.failures)
    assert metrics.digest_inclusion.numerator < metrics.digest_inclusion.denominator


def test_hidden_ladder_records_are_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: hide every procedural record from the ladder; the min count
    must fail."""
    from provalume.sdk.client import Provalume

    original = Provalume.memory_records

    def filtered(self: Provalume, *args: object, **kwargs: object) -> list:
        return [m for m in original(self, *args, **kwargs) if str(m.memory_type) != "procedural"]

    monkeypatch.setattr(Provalume, "memory_records", filtered)
    result = _run(load_fixture(FIXTURES / "fix-lands"), 1, Metrics())
    assert not result.passed
    assert any("procedural/integrated count 0 < 3" in f for f in result.failures)


def test_recall_regression_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: recall returns nothing; the probe must fail and the coverage
    counter must move."""
    from provalume.sdk.client import Provalume

    original = Provalume.recall

    def selective(self: Provalume, query: str = "", **kwargs: object) -> object:
        # Probe calls use limit=10 and no scope kwargs; the digest path
        # (limit=20, branch/task scoped) must stay live or replay itself dies.
        if kwargs.get("limit") == 10 and "branch" not in kwargs:
            return []
        return original(self, query, **kwargs)

    monkeypatch.setattr(Provalume, "recall", selective)
    metrics = Metrics()
    result = _run(load_fixture(FIXTURES / "fix-lands"), 1, metrics)
    assert not result.passed
    assert any("0 results, expected 1" in f for f in result.failures)
    assert (metrics.recall_coverage.numerator, metrics.recall_coverage.denominator) == (0, 1)


def test_misscoped_event_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: write attempt events under the wrong task; the envelope-scoped
    journal comparison must fail."""
    from provalume.integrations.orkestra import OrkestraAdapter

    original = OrkestraAdapter.attempt_completed

    def misscoped(self: OrkestraAdapter, **kwargs: object) -> object:
        kwargs["task_id"] = "task_WRONG"
        return original(self, **kwargs)

    monkeypatch.setattr(OrkestraAdapter, "attempt_completed", misscoped)
    result = _run(load_fixture(FIXTURES / "fix-lands"), 1, Metrics())
    assert not result.passed
    assert any("journal: attempt.completed" in f and "task_WRONG" in f for f in result.failures)


def test_spurious_event_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: a landing also writes an event the call log never implied;
    the two-way journal comparison must fail."""
    from provalume.integrations.orkestra import OrkestraAdapter

    original = OrkestraAdapter.integration_landed

    def chatty(self: OrkestraAdapter, **kwargs: object) -> object:
        event = original(self, **kwargs)
        self.pv.record_decision(selected="bogus", question="never asked")
        return event

    monkeypatch.setattr(OrkestraAdapter, "integration_landed", chatty)
    result = _run(load_fixture(FIXTURES / "fix-lands"), 1, Metrics())
    assert not result.passed
    assert any("journal: human.decision" in f for f in result.failures)


# --- load-time refusals -------------------------------------------------------


def _fixture_copy(tmp_path: Path, name: str = "fix-lands") -> Path:
    source = FIXTURES / name
    target = tmp_path / name
    target.mkdir()
    (target / "calls.jsonl").write_text((source / "calls.jsonl").read_text())
    (target / "expectations.json").write_text((source / "expectations.json").read_text())
    return target


def _rewrite_expectations(target: Path, mutate) -> None:
    expectations = json.loads((target / "expectations.json").read_text())
    mutate(expectations)
    (target / "expectations.json").write_text(json.dumps(expectations))


def test_truncated_fixture_is_refused(tmp_path: Path) -> None:
    target = _fixture_copy(tmp_path)
    calls = (target / "calls.jsonl").read_text().splitlines()
    record = json.loads(calls[5])
    record["kwargs"]["excerpt"] = "cut short …[truncated]"
    calls[5] = json.dumps(record, ensure_ascii=False)
    (target / "calls.jsonl").write_text("\n".join(calls) + "\n")
    with pytest.raises(ValueError, match="truncated"):
        load_fixture(target)


def test_checkpoint_on_a_write_call_is_refused(tmp_path: Path) -> None:
    target = _fixture_copy(tmp_path)
    _rewrite_expectations(target, lambda e: e["checkpoints"][0].update(call=5))
    with pytest.raises(ValueError, match="not a decision-point read"):
        load_fixture(target)


def test_unrecognised_expectation_key_is_refused(tmp_path: Path) -> None:
    """A misspelled key must be an error, never a silently skipped check."""
    target = _fixture_copy(tmp_path)
    _rewrite_expectations(target, lambda e: e["checkpoints"][3]["expect"].update(occurences=1))
    with pytest.raises(ValueError, match="unrecognised expectation key"):
        load_fixture(target)


def test_empty_contains_is_refused(tmp_path: Path) -> None:
    """`contains: []` is a guaranteed win, which is no assertion at all."""
    target = _fixture_copy(tmp_path)
    _rewrite_expectations(target, lambda e: e["checkpoints"][2]["expect"].update(contains=[]))
    with pytest.raises(ValueError, match="non-empty list"):
        load_fixture(target)


def test_ladder_entry_without_bounds_is_refused(tmp_path: Path) -> None:
    target = _fixture_copy(tmp_path)

    def strip_bounds(expectations: dict) -> None:
        for probe in expectations["probes"]:
            if probe["kind"] == "ladder":
                for entry in probe["expect_counts"]:
                    entry.pop("min", None)
                    entry.pop("max", None)

    _rewrite_expectations(target, strip_bounds)
    with pytest.raises(ValueError, match="asserts nothing"):
        load_fixture(target)


def test_fixture_without_checkpoints_is_refused(tmp_path: Path) -> None:
    target = _fixture_copy(tmp_path)
    _rewrite_expectations(target, lambda e: e.update(checkpoints=[]))
    with pytest.raises(ValueError, match="measures nothing"):
        load_fixture(target)


def test_run_one_names_available_fixtures() -> None:
    with pytest.raises(ValueError, match="fix-lands"):
        run_one("no-such-trajectory", root=FIXTURES)


def test_run_one_refuses_ambiguity() -> None:
    with pytest.raises(ValueError, match="ambiguous"):
        run_one("e", root=FIXTURES)  # env-gotcha, repeat-blocked, …


def test_run_all_without_fixtures_says_where_it_looked(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="trajectory fixtures"):
        run_all(tmp_path / "missing")
