"""Score the M5 measurement: system results vs independent labels.

Metrics and the kill criterion are fixed in the session's DECISIONS D17,
written before any number existed. This script computes; it does not tune.

    .venv/bin/python evals/freshness_precision/score.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent

#: Fixed before the first measurement (D17): stale is the one freshness
#: state that asserts rather than invites, and more than 1 false stale in 5
#: teaches users to ignore it — the feature is then worse than nothing.
STALE_PRECISION_KILL_FLOOR = 0.80


def main() -> int:
    results = {
        row["case_id"]: row
        for row in json.loads((EVAL_DIR / "results" / "results.json").read_text())
    }
    labels = {
        row["case_id"]: row
        for row in json.loads((EVAL_DIR / "labeling" / "labels.json").read_text())
    }
    missing = sorted(set(results) ^ set(labels))
    if missing:
        print(f"case sets differ: {missing}")
        return 2

    uncertain = sorted(c for c, row in labels.items() if row["label"] == "uncertain")
    decided = {c for c, row in labels.items() if row["label"] in {"yes", "no"}}

    def labeled_yes(case_id: str) -> bool:
        return labels[case_id]["label"] == "yes"

    stale = [c for c in decided if results[c]["freshness_final"] == "stale"]
    suspect_after_scan = [c for c in decided if results[c]["freshness_after_scan"] == "suspect"]
    flagged = [
        c
        for c in decided
        if results[c]["freshness_after_scan"] == "suspect"
        or results[c]["freshness_final"] == "stale"
    ]
    should = [c for c in decided if labeled_yes(c)]
    # A should-invalidate case whose re-run reported `passed` is worse than
    # a miss: the re-run actively laundered a suspect back to current.
    # Added to the report after the first measurement pass exposed the
    # class (harness-side bytecode staleness) — an extra failure surface,
    # never a relaxation (D17 amendment).
    false_pass = sorted(c for c in should if results[c]["rerun_outcome"] == "passed")
    false_current = sorted(
        c
        for c in should
        if results[c]["freshness_after_scan"] == "current"
        and results[c]["freshness_final"] == "current"
    )

    def precision(cases: list[str]) -> float | None:
        return None if not cases else sum(labeled_yes(c) for c in cases) / len(cases)

    stale_precision = precision(stale)
    metrics = {
        "cases": len(results),
        "uncertain_labels_excluded": uncertain,
        "stale_precision": stale_precision,
        "stale_count": len(stale),
        "false_stale_cases": sorted(c for c in stale if not labeled_yes(c)),
        "suspect_precision_informational": precision(suspect_after_scan),
        "suspect_count_after_scan": len(suspect_after_scan),
        "recall_flagged_over_should": (
            None if not should else sum(c in flagged for c in should) / len(should)
        ),
        "should_invalidate_count": len(should),
        "false_pass_cases": false_pass,
        "false_current_cases": false_current,
        "kill_floor_stale_precision": STALE_PRECISION_KILL_FLOOR,
        "kill_criterion_tripped": (
            stale_precision is not None and stale_precision < STALE_PRECISION_KILL_FLOOR
        ),
    }
    out = EVAL_DIR / "results" / "metrics.json"
    out.write_text(json.dumps(metrics, indent=2) + "\n")

    print(json.dumps(metrics, indent=2))
    if metrics["kill_criterion_tripped"]:
        print("\nKILL CRITERION TRIPPED (D17): report and halt; do not tune.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
