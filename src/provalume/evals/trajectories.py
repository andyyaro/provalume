"""The trajectory suite — real captured runs, replayed through the live adapter.

Where the twenty scenarios (``replay.py``) drive the engine with fixtures this
project wrote, the trajectory suite drives it with fixtures this project
*captured*: the ordered log of every ``OrkestraAdapter`` call a real Orkestra
dogfood run made — real worktrees, real failing commands, real retries, real
landings. The scenario scripts were authored; the calls, excerpts, and
multiplicity are whatever the runs actually did (ADR-0019).

Replay re-executes the captured calls, in order, against a fresh in-memory
database through the same adapter methods the integration uses — deliberately
not the JSONL import path, whose admission gate caps foreign events at
``observed`` and would bypass the live write path under test.

Scoring happens at **decision points**: the captured ``brief_digest`` and
``preflight`` reads made at the moments an orchestrator was about to dispatch
work. Expectations state what memory owed the agent at that moment, derived
from the trajectory itself, and feed the shared metric counters so every rate
keeps its denominator. Post-trajectory probes ask what memory holds after the
dust settles; they feed the same counters, except probes marked
``known_limitation``, which exercise a documented gap, pass by missing, and
touch no counter at all. Expectation files are schema-validated at load time —
an unrecognised key is an error, never a silently skipped check.

What this suite does not measure: whether an agent reads any of it and does
better. The practice agents in captured runs are scripted placeholders, so
``task_completion`` and friends keep their zero denominators (LIMITATIONS §1).

Fidelity limits are stated in ADR-0019. The load-bearing one: replay uses
``git=None``, so events lose the commit anchors the live run stamped, and
digest text is measurably less informative than at capture. Replay therefore
compares each read against the captured return and reports divergence as
notes — visible, not scored.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from provalume.evals.metrics import Metrics
from provalume.evals.replay import ScenarioResult

#: The shim marks values it had to cut; a fixture containing one is corrupt,
#: because replay would feed the engine different bytes than the run did.
TRUNCATION_SENTINEL = "…[truncated]"

READ_METHODS = frozenset({"brief_digest", "preflight"})

#: Journal event type each replayed write call must land, exactly once.
#: ``verification`` and ``review_verdict`` are mapped in :func:`_event_for`
#: because their event type depends on the kwargs.
_EVENT_FOR_CALL = {
    "run_started": "run.started",
    "run_completed": "run.completed",
    "task_completed": "task.completed",
    "attempt_completed": "attempt.completed",
    "human_decision": "human.decision",
    "integration_landed": "integration.landed",
    "reviewer_finding": "review.finding",
    "integration_reverted": "integration.reverted",
    "branch_rejected": "branch.rejected",
}

_PREFLIGHT_KEYS = frozenset(
    {"matched", "occurrences", "min_confidence", "resolved", "resolution_names_commit_of_call"}
)
_DIGEST_KEYS = frozenset({"items", "min_items", "contains"})
_RECALL_KEYS = frozenset({"total", "min_total", "top_type", "top_trust"})
_PROBE_KINDS = frozenset({"preflight", "digest", "recall", "ladder"})


def _event_for(method: str, kwargs: dict[str, Any]) -> str | None:
    if method == "verification":
        return "verification.passed" if kwargs.get("passed") else "verification.failed"
    if method == "review_verdict":
        if kwargs.get("approved"):
            return "review.approved"
        if kwargs.get("changes_requested"):
            return "review.changes_requested"
        return "review.rejected"
    return _EVENT_FOR_CALL.get(method)


@dataclass
class TrajectoryFixture:
    name: str
    calls: list[dict[str, Any]]
    expectations: dict[str, Any]


@dataclass
class TrajectoryResults:
    trajectories: list[dict[str, Any]] = field(default_factory=list)
    metrics: Metrics = field(default_factory=Metrics)

    @property
    def passed(self) -> bool:
        return bool(self.trajectories) and all(t["passed"] for t in self.trajectories)

    @property
    def pass_count(self) -> int:
        return sum(1 for t in self.trajectories if t["passed"])

    def summary(self) -> str:
        return f"{self.pass_count}/{len(self.trajectories)} trajectories passed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "trajectories": self.trajectories,
            "metrics": self.metrics.as_dict(),
            "passed": self.passed,
            "pass_count": self.pass_count,
            "total": len(self.trajectories),
            "summary": self.summary(),
            "note": (
                "All figures are Provalume measured against Provalume, replaying "
                "call logs captured from real Orkestra runs. No comparison against "
                "another system is made or implied."
            ),
        }


# --- Loading ----------------------------------------------------------------


def _scan_for_truncation(value: Any) -> bool:
    if isinstance(value, str):
        return value.endswith(TRUNCATION_SENTINEL)
    if isinstance(value, dict):
        return any(_scan_for_truncation(v) for v in value.values())
    if isinstance(value, list):
        return any(_scan_for_truncation(v) for v in value)
    return False


def _require_keys(where: str, expect: dict[str, Any], allowed: frozenset[str]) -> None:
    """An unrecognised expectation key must be an error, not a skipped check —
    a misspelled key that silently asserts nothing is the 'assertion that
    cannot fail' defect class this repository keeps paying for."""
    if not expect:
        raise ValueError(f"{where}: empty expectation asserts nothing")
    unknown = set(expect) - allowed
    if unknown:
        raise ValueError(f"{where}: unrecognised expectation key(s) {sorted(unknown)}")


def _validate_contains(where: str, expect: dict[str, Any]) -> None:
    if "contains" in expect:
        contains = expect["contains"]
        if (
            not isinstance(contains, list)
            or not contains
            or not all(isinstance(s, str) and s for s in contains)
        ):
            raise ValueError(f"{where}: 'contains' must be a non-empty list of non-empty strings")


def _validate_landing_reference(
    where: str, expect: dict[str, Any], calls: list[dict[str, Any]]
) -> None:
    if "resolution_names_commit_of_call" in expect:
        target = expect["resolution_names_commit_of_call"]
        if not (isinstance(target, int) and 0 <= target < len(calls)):
            raise ValueError(f"{where}: resolution_names_commit_of_call {target!r} out of range")
        if not calls[target].get("kwargs", {}).get("commit_sha"):
            raise ValueError(f"{where}: call {target} carries no commit_sha to name a landing by")


def load_fixture(directory: Path) -> TrajectoryFixture:
    calls_path = directory / "calls.jsonl"
    expectations_path = directory / "expectations.json"
    calls = [json.loads(line) for line in calls_path.read_text().splitlines() if line]
    expectations = json.loads(expectations_path.read_text())

    for index, call in enumerate(calls):
        if _scan_for_truncation(call):
            raise ValueError(
                f"{calls_path}: call {index} was truncated at capture; "
                "the fixture cannot be replayed faithfully"
            )
        if call.get("args"):
            raise ValueError(
                f"{calls_path}: call {index} carries positional args, "
                "which replay would silently drop"
            )

    checkpoints = expectations.get("checkpoints", ())
    if not checkpoints:
        raise ValueError(f"{expectations_path}: a fixture with no checkpoints measures nothing")
    for checkpoint in checkpoints:
        index = checkpoint["call"]
        where = f"{expectations_path}: checkpoint@{index}"
        if not 0 <= index < len(calls):
            raise ValueError(f"{where}: call index out of range")
        method = calls[index].get("method")
        expect = checkpoint.get("expect", {})
        if method == "preflight":
            _require_keys(where, expect, _PREFLIGHT_KEYS)
            if "matched" not in expect:
                raise ValueError(f"{where}: preflight expectation needs 'matched'")
            _validate_landing_reference(where, expect, calls)
        elif method == "brief_digest":
            _require_keys(where, expect, _DIGEST_KEYS)
            _validate_contains(where, expect)
        else:
            raise ValueError(f"{where}: call is {method!r}, not a decision-point read")

    for number, probe in enumerate(expectations.get("probes", ())):
        kind = probe.get("kind")
        where = f"{expectations_path}: probe#{number}"
        if kind not in _PROBE_KINDS:
            raise ValueError(f"{where}: unknown probe kind {kind!r}")
        if kind == "preflight":
            target = probe.get("command_of_call")
            if not (isinstance(target, int) and 0 <= target < len(calls)):
                raise ValueError(f"{where}: command_of_call {target!r} out of range")
            if not calls[target].get("kwargs", {}).get("command"):
                raise ValueError(f"{where}: call {target} carries no command")
            _require_keys(where, probe.get("expect", {}), _PREFLIGHT_KEYS)
            if "matched" not in probe["expect"]:
                raise ValueError(f"{where}: preflight expectation needs 'matched'")
            _validate_landing_reference(where, probe["expect"], calls)
        elif kind == "digest":
            if not probe.get("query"):
                raise ValueError(f"{where}: digest probe needs a query")
            _require_keys(where, probe.get("expect", {}), _DIGEST_KEYS)
            _validate_contains(where, probe["expect"])
        elif kind == "recall":
            if not probe.get("query"):
                raise ValueError(f"{where}: recall probe needs a query")
            _require_keys(where, probe.get("expect", {}), _RECALL_KEYS)
        else:  # ladder
            entries = probe.get("expect_counts")
            if not entries:
                raise ValueError(f"{where}: ladder probe needs expect_counts")
            for entry in entries:
                if "type" not in entry or "trust" not in entry:
                    raise ValueError(f"{where}: ladder entry needs 'type' and 'trust'")
                if "min" not in entry and "max" not in entry:
                    raise ValueError(f"{where}: ladder entry asserts nothing without min or max")
    return TrajectoryFixture(directory.name, calls, expectations)


def discover(root: Path) -> list[Path]:
    """Fixture directories under ``root``, sorted by name for stable ordering."""
    return sorted(
        (p for p in root.iterdir() if (p / "calls.jsonl").exists()),
        key=lambda p: p.name,
    )


# --- Replay -----------------------------------------------------------------


def _decode_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    # The shim serialises tuples as lists; the adapter's sequence parameters
    # (``rejected``, ``files``, ``findings``) take tuples.
    return {k: tuple(v) if isinstance(v, list) else v for k, v in kwargs.items()}


def _context_fields(captured: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in captured.items() if not k.startswith("__")}


def _drift(index: int, method: str, captured: dict[str, Any], replayed: Any) -> str | None:
    """One line naming how a replayed read differs from the captured return.

    Divergence is expected wherever ``git=None`` changes what the engine
    writes (ADR-0019); reporting it as a note keeps it visible without making
    the capture an oracle.
    """
    if method == "brief_digest":
        captured_items = captured.get("items")
        captured_count = len(captured_items) if isinstance(captured_items, list) else None
        if captured_count != len(replayed.items) or captured.get("chars_used") != (
            replayed.chars_used
        ):
            return (
                f"drift@{index} brief_digest: captured items={captured_count} "
                f"chars={captured.get('chars_used')}, replayed items={len(replayed.items)} "
                f"chars={replayed.chars_used}"
            )
        return None
    captured_matches = captured.get("matches") or []
    captured_occurrences = [m.get("occurrences") for m in captured_matches if isinstance(m, dict)]
    replayed_occurrences = [m.occurrences for m in replayed.matches]
    if captured.get("matched") != replayed.matched or (
        captured_occurrences != replayed_occurrences
    ):
        return (
            f"drift@{index} preflight: captured matched={captured.get('matched')} "
            f"occurrences={captured_occurrences}, replayed matched={replayed.matched} "
            f"occurrences={replayed_occurrences}"
        )
    return None


class _TrajectoryRun:
    """One trajectory's replay: fresh database, live adapter, scored reads."""

    def __init__(self, fixture: TrajectoryFixture, metrics: Metrics) -> None:
        from provalume.store.db import open_database

        self.fixture = fixture
        self.metrics = metrics
        self.failures: list[str] = []
        self.notes: list[str] = []
        self.db = open_database(":memory:")
        self.pv: Any = None
        self.adapter: Any = None
        self.results: dict[int, Any] = {}

    def check(self, condition: bool, message: str) -> bool:
        if not condition:
            self.failures.append(message)
        return condition

    # -- replaying the call log -------------------------------------------

    def _open_client(self, context: dict[str, Any]) -> None:
        from provalume.integrations.orkestra import OrkestraAdapter, OrkestraContext
        from provalume.sdk.client import Provalume

        fields = _context_fields(context)
        self.pv = Provalume(self.db, project_id=fields["project_id"], git=None)
        self.adapter = OrkestraAdapter(self.pv, OrkestraContext(**fields))

    def replay(self) -> None:
        for index, call in enumerate(self.fixture.calls):
            method = call["method"]
            if method == "__init__":
                self._open_client(call["context"])
                continue
            if self.adapter is None:
                self.failures.append(f"call {index} arrives before any __init__")
                return
            kwargs = _decode_kwargs(call.get("kwargs", {}))
            start = time.perf_counter()
            try:
                result = getattr(self.adapter, method)(**kwargs)
            except Exception as exc:  # reported as a failure, never raised
                self.failures.append(f"call {index} {method} raised {type(exc).__name__}: {exc}")
                return
            elapsed = (time.perf_counter() - start) * 1000
            if method in READ_METHODS:
                self.metrics.retrieval_latency.observe(elapsed)
                self.results[index] = result
                if method == "brief_digest":
                    self.metrics.observe_context(result.chars_used)
                captured = call.get("result")
                if isinstance(captured, dict):
                    drift = _drift(index, method, captured, result)
                    if drift:
                        self.notes.append(drift)
            else:
                self.metrics.write_latency.observe(elapsed)

    # -- scoring -----------------------------------------------------------

    def _landing_sha(self, call_index: int) -> str:
        sha = self.fixture.calls[call_index].get("kwargs", {}).get("commit_sha", "")
        return str(sha)

    def _score_preflight(
        self, label: str, result: Any, expect: dict[str, Any], *, count: bool = True
    ) -> None:
        should_match = expect["matched"]
        if should_match:
            if count:
                self.metrics.repeated_error.observe(not result.matched)
            if not result.matched:
                # A silent gate misses every follow-on expectation too; keeping
                # those denominators aligned means the rates stay comparable
                # instead of a miss quietly shrinking a denominator.
                if count and "occurrences" in expect:
                    self.metrics.occurrence_fidelity.observe(False)
                if count and "resolution_names_commit_of_call" in expect:
                    self.metrics.resolution_surfacing.observe(False)
                self.check(False, f"{label}: expected a warning, gate silent")
                return
            top = max(result.matches, key=lambda m: m.confidence)
            if "occurrences" in expect:
                hit = top.occurrences == expect["occurrences"]
                if count:
                    self.metrics.occurrence_fidelity.observe(hit)
                self.check(
                    hit,
                    f"{label}: occurrences {top.occurrences}, expected {expect['occurrences']}",
                )
            if "min_confidence" in expect:
                self.check(
                    top.confidence >= expect["min_confidence"],
                    f"{label}: confidence {top.confidence} below {expect['min_confidence']}",
                )
            if "resolution_names_commit_of_call" in expect:
                sha = self._landing_sha(expect["resolution_names_commit_of_call"])
                hit = bool(top.what_later_worked) and top.resolution_commit_sha == sha
                if count:
                    self.metrics.resolution_surfacing.observe(hit)
                self.check(
                    hit,
                    f"{label}: resolution should name landing {sha[:12]}, "
                    f"got {top.resolution_commit_sha[:12]!r} "
                    f"(what_later_worked={top.what_later_worked[:60]!r})",
                )
            elif expect.get("resolved") is True:
                self.check(
                    bool(top.what_later_worked),
                    f"{label}: expected a resolution, none presented",
                )
            elif expect.get("resolved") is False:
                self.check(
                    all(not m.what_later_worked for m in result.matches),
                    f"{label}: presented a resolution while still unresolved",
                )
        else:
            if count:
                self.metrics.false_warnings.observe(result.matched)
            self.check(not result.matched, f"{label}: warned with no prior failure")

    def _score_digest(
        self, label: str, result: Any, expect: dict[str, Any], *, count: bool = True
    ) -> None:
        if result.char_budget:
            self.check(
                result.chars_used <= result.char_budget,
                f"{label}: digest {result.chars_used} chars exceeds budget {result.char_budget}",
            )
        if "items" in expect:
            self.check(
                len(result.items) == expect["items"],
                f"{label}: digest has {len(result.items)} items, expected {expect['items']}",
            )
        if "min_items" in expect:
            self.check(
                len(result.items) >= expect["min_items"],
                f"{label}: digest has {len(result.items)} items, "
                f"expected at least {expect['min_items']}",
            )
        if "contains" in expect:
            missing = [s for s in expect["contains"] if s not in result.text]
            if count:
                self.metrics.digest_inclusion.observe(not missing)
            self.check(not missing, f"{label}: digest missing {missing}")

    def score_checkpoints(self) -> None:
        for checkpoint in self.fixture.expectations.get("checkpoints", ()):
            index = checkpoint["call"]
            expect = checkpoint["expect"]
            label = f"checkpoint@{index}"
            if index not in self.results:
                self.failures.append(f"{label}: no replayed result (replay aborted?)")
                continue
            result = self.results[index]
            if self.fixture.calls[index]["method"] == "preflight":
                self._score_preflight(label, result, expect)
            else:
                self._score_digest(label, result, expect)

    # -- probes ------------------------------------------------------------

    def _score_recall(self, label: str, probe: dict[str, Any]) -> None:
        expect = probe["expect"]
        known_miss = "known_limitation" in probe
        start = time.perf_counter()
        response = self.pv.recall(probe["query"], limit=10)
        self.metrics.retrieval_latency.observe((time.perf_counter() - start) * 1000)
        results = list(response)
        if "total" in expect:
            self.check(
                len(results) == expect["total"],
                f"{label}: {len(results)} results, expected {expect['total']}",
            )
        if "min_total" in expect:
            self.check(
                len(results) >= expect["min_total"],
                f"{label}: {len(results)} results, expected >= {expect['min_total']}",
            )
        if not known_miss and (expect.get("total", 0) or expect.get("min_total", 0)):
            self.metrics.recall_coverage.observe(bool(results))
        if results and "top_type" in expect:
            hit = str(results[0].memory_type) == expect["top_type"]
            if not known_miss:
                self.metrics.recall_precision.observe(hit)
            self.check(
                hit,
                f"{label}: top result is {results[0].memory_type}, expected {expect['top_type']}",
            )
        if results and "top_trust" in expect:
            self.check(
                str(results[0].trust_state) == expect["top_trust"],
                f"{label}: top trust {results[0].trust_state}, expected {expect['top_trust']}",
            )

    def _score_ladder(self, label: str, probe: dict[str, Any]) -> None:
        counts: dict[tuple[str, str], int] = {}
        for record in self.pv.memory_records():
            key = (str(record.memory_type), str(record.trust_state))
            counts[key] = counts.get(key, 0) + 1
        for expected in probe["expect_counts"]:
            key = (expected["type"], expected["trust"])
            actual = counts.get(key, 0)
            if "min" in expected:
                self.check(
                    actual >= expected["min"],
                    f"{label}: {key[0]}/{key[1]} count {actual} < {expected['min']}",
                )
            if "max" in expected:
                self.check(
                    actual <= expected["max"],
                    f"{label}: {key[0]}/{key[1]} count {actual} > {expected['max']}",
                )

    def score_probes(self) -> None:
        for number, probe in enumerate(self.fixture.expectations.get("probes", ())):
            kind = probe["kind"]
            label = f"probe#{number}({kind})"
            known_miss = "known_limitation" in probe
            if known_miss:
                self.notes.append(f"{label} exercised: {probe['known_limitation']}")
            if kind == "preflight":
                command = self.fixture.calls[probe["command_of_call"]]["kwargs"]["command"]
                start = time.perf_counter()
                result = self.adapter.preflight(command=command, record=False)
                self.metrics.retrieval_latency.observe((time.perf_counter() - start) * 1000)
                self._score_preflight(label, result, probe["expect"], count=not known_miss)
            elif kind == "digest":
                start = time.perf_counter()
                result = self.adapter.brief_digest(
                    query=probe["query"], char_budget=probe.get("char_budget", 2000)
                )
                self.metrics.retrieval_latency.observe((time.perf_counter() - start) * 1000)
                self.metrics.observe_context(result.chars_used)
                self._score_digest(label, result, probe["expect"], count=not known_miss)
            elif kind == "recall":
                self._score_recall(label, probe)
            else:
                self._score_ladder(label, probe)

    # -- integrity ---------------------------------------------------------

    def verify_journal(self) -> None:
        """Every replayed write must have landed, in the right envelope, and
        nothing else may have been written.

        The integration's wiring is best-effort, so the suite asserts writes
        *happened* — not that nothing raised. The comparison is two-way and
        scoped: expected events are keyed (type, run_id, task_id), derived by
        walking the call log with its ``__init__`` contexts, so an event
        written under the wrong run or task is a failure, and so is an event
        type the call log never implied. ``warning.shown`` is checked as an
        aggregate count (one per recorded matching decision point).
        """
        expected: dict[tuple[str, str, str], int] = {}
        context_run_id = ""
        for call in self.fixture.calls:
            if call["method"] == "__init__":
                context_run_id = _context_fields(call["context"]).get("run_id") or ""
                continue
            kwargs = call.get("kwargs", {})
            event_type = _event_for(call["method"], kwargs)
            if event_type:
                key = (
                    event_type,
                    str(kwargs.get("run_id") or context_run_id),
                    str(kwargs.get("task_id") or ""),
                )
                expected[key] = expected.get(key, 0) + 1
        expected_warnings = sum(
            1
            for checkpoint in self.fixture.expectations.get("checkpoints", ())
            if self.fixture.calls[checkpoint["call"]]["method"] == "preflight"
            and self.fixture.calls[checkpoint["call"]].get("kwargs", {}).get("record", True)
            and checkpoint["expect"].get("matched")
        )

        actual: dict[tuple[str, str, str], int] = {}
        warnings_shown = 0
        for event in self.pv.events():
            event_type = str(event.event_type)
            if event_type == "warning.shown":
                warnings_shown += 1
                continue
            key = (event_type, str(event.run_id or ""), str(event.task_id or ""))
            actual[key] = actual.get(key, 0) + 1
        for key in sorted(set(expected) | set(actual)):
            self.check(
                actual.get(key, 0) == expected.get(key, 0),
                f"journal: {key[0]} (run={key[1] or '-'}, task={key[2] or '-'}) "
                f"count {actual.get(key, 0)}, expected {expected.get(key, 0)}",
            )
        self.check(
            warnings_shown == expected_warnings,
            f"journal: warning.shown count {warnings_shown}, expected {expected_warnings}",
        )

    def verify_integrity(self) -> None:
        start = time.perf_counter()
        report = self.pv.audit(deep=True)
        self.metrics.rebuild_latency.observe((time.perf_counter() - start) * 1000)
        self.check(
            report.ok,
            "audit: " + "; ".join(f.message for f in report.errors),
        )

    def close(self) -> None:
        self.db.close()


def _run(fixture: TrajectoryFixture, index: int, metrics: Metrics) -> ScenarioResult:
    start = time.perf_counter()
    run = _TrajectoryRun(fixture, metrics)
    try:
        run.replay()
        if not run.failures:
            run.score_checkpoints()
            run.score_probes()
            run.verify_journal()
            run.verify_integrity()
    except Exception as exc:  # a broken harness is a failed result, not a crash
        run.failures.append(f"trajectory raised {type(exc).__name__}: {exc}")
    finally:
        run.close()
    elapsed = (time.perf_counter() - start) * 1000
    return ScenarioResult(
        id=index,
        name=fixture.name,
        description=fixture.expectations.get("description", ""),
        passed=not run.failures,
        failures=run.failures,
        notes=run.notes,
        elapsed_ms=elapsed,
    )


DEFAULT_FIXTURES = Path("evals") / "fixtures" / "trajectories"


def run_all(root: Path | None = None) -> TrajectoryResults:
    root = root or DEFAULT_FIXTURES
    if not root.is_dir():
        raise FileNotFoundError(
            f"no trajectory fixtures at {root} — run from a repository checkout "
            "or pass the fixtures directory explicitly"
        )
    results = TrajectoryResults()
    for index, directory in enumerate(discover(root), start=1):
        fixture = load_fixture(directory)
        results.trajectories.append(_run(fixture, index, results.metrics).as_dict())
    return results


def run_one(name: str, root: Path | None = None) -> TrajectoryResults:
    root = root or DEFAULT_FIXTURES
    available = [p.name for p in discover(root)] if root.is_dir() else []
    exact = [n for n in available if n == name]
    matches = exact or ([n for n in available if name in n] if name else [])
    if not matches:
        raise ValueError(
            f"no trajectory matches {name!r}; available: {', '.join(available) or 'none'}"
        )
    if len(matches) > 1:
        raise ValueError(f"{name!r} is ambiguous; matches: {', '.join(matches)}")
    results = TrajectoryResults()
    fixture = load_fixture(root / matches[0])
    results.trajectories.append(_run(fixture, 1, results.metrics).as_dict())
    return results
