"""Aggregate the real-history measurement (D21). Computes; never tunes.

    .venv/bin/python evals/freshness_realworld/report.py

Confidence intervals are Wilson 95%. The unit is a (record, commit) point;
points are correlated within record and within commit, so the intervals
understate uncertainty — stated here once and again in the output.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
RESULTS = EVAL_DIR / "results"


def wilson(k: int, n: int) -> tuple[float, float, float] | None:
    """(proportion, low, high) at 95%, or None for an empty denominator."""
    if n == 0:
        return None
    z = 1.959964
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (p, max(0.0, centre - half), min(1.0, centre + half))


def _fmt(stat: tuple[float, float, float] | None, k: int, n: int) -> str:
    if stat is None:
        return f"undefined (0 denominator; k={k})"
    p, lo, hi = stat
    return f"{p:.3f} [{lo:.3f}, {hi:.3f}]  ({k}/{n})"


def analyze(rows: list[dict], records: dict[str, dict]) -> dict:
    usable = [r for r in rows if r["truth"] in {"pass", "fail"}]
    errors = len(rows) - len(usable)
    fails = [r for r in usable if r["truth"] == "fail"]
    stale = [r for r in usable if r["state_final"] == "stale"]
    fresh = [r for r in usable if r["fresh_suspect"]]

    stale_true = sum(r["truth"] == "fail" for r in stale)
    recalled = sum(r["state_final"] == "stale" for r in fails)
    false_current = sum(r["state_final"] == "current" for r in fails)
    fresh_true = sum(r["truth"] == "fail" for r in fresh)

    flaky = [
        r
        for r in usable
        if r["rerun_outcome"] in {"passed", "failed"}
        and (r["rerun_outcome"] == "passed") != (r["truth"] == "pass")
    ]

    by_method: dict[str, dict] = {}
    for r in fresh:
        method = str(records.get(r["record_id"], {}).get("radius_method"))
        cell = by_method.setdefault(method, {"fresh": 0, "fresh_true": 0})
        cell["fresh"] += 1
        cell["fresh_true"] += r["truth"] == "fail"

    return {
        "points": len(usable),
        "errors_excluded": errors,
        "truth_fail_points": len(fails),
        "stale_points": len(stale),
        "stale_precision": wilson(stale_true, len(stale)),
        "stale_precision_k": stale_true,
        "stale_recall": wilson(recalled, len(fails)),
        "stale_recall_k": recalled,
        "false_current_rate": wilson(false_current, len(fails)),
        "false_current_k": false_current,
        "state_at_fail": dict(Counter(r["state_final"] for r in fails)),
        "suspect_precision_at_scan_JUDGMENT_LADEN": wilson(fresh_true, len(fresh)),
        "suspect_precision_k": fresh_true,
        "fresh_suspect_points": len(fresh),
        "reason_codes_at_fresh_suspect": dict(Counter(str(r["reason_code"]) for r in fresh)),
        "by_radius_method": {
            m: {**cell, "precision": wilson(cell["fresh_true"], cell["fresh"])}
            for m, cell in sorted(by_method.items())
        },
        "flaky_disagreements": len(flaky),
    }


def main() -> None:
    pooled_rows: list[dict] = []
    pooled_records: dict[str, dict] = {}
    per_repo: dict[str, dict] = {}
    for path in sorted(RESULTS.glob("*.json")):
        if path.name == "metrics.json":
            continue
        data = json.loads(path.read_text())
        records = data["setup"].get("records", {})
        per_repo[data["setup"]["repo"]] = {
            "setup": {k: v for k, v in data["setup"].items() if k in {"sampling", "wall_clock_s"}},
            "records": len(records),
            **analyze(data["rows"], records),
        }
        pooled_rows.extend(data["rows"])
        pooled_records.update(records)

    pooled = analyze(pooled_rows, pooled_records)
    out = {"pooled": pooled, "per_repo": per_repo}
    (RESULTS / "metrics.json").write_text(json.dumps(out, indent=2, default=str) + "\n")

    print(
        "CAVEAT: Wilson 95% CIs; points are correlated within record and "
        "commit, so intervals understate uncertainty.\n"
    )
    for name, m in [*per_repo.items(), ("POOLED", pooled)]:
        print(f"== {name}: {m['points']} points, {m.get('records', '')} records")
        print(f"   truth=fail points : {m['truth_fail_points']}")
        print(
            f"   stale precision   : "
            f"{_fmt(m['stale_precision'], m['stale_precision_k'], m['stale_points'])}"
        )
        print(
            f"   stale recall      : "
            f"{_fmt(m['stale_recall'], m['stale_recall_k'], m['truth_fail_points'])}"
        )
        print(
            f"   false-current rate: "
            f"{_fmt(m['false_current_rate'], m['false_current_k'], m['truth_fail_points'])}"
        )
        print(f"   state at fail     : {m['state_at_fail']}")
        suspect_line = _fmt(
            m["suspect_precision_at_scan_JUDGMENT_LADEN"],
            m["suspect_precision_k"],
            m["fresh_suspect_points"],
        )
        print(f"   suspect precision (JUDGMENT-LADEN analogue): {suspect_line}")
        print(f"   by radius method  : {m['by_radius_method']}")
        print(f"   flaky disagreement: {m['flaky_disagreements']}")
        print()


if __name__ == "__main__":
    main()
