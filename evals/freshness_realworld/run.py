"""Real-history freshness measurement: computed ground truth, no labeler.

Protocol locked in the session's DECISIONS D21 before any number existed.
For each qualified repository: records are established at commit N by
running real test commands through the real SDK path; every first-parent
commit N+1..HEAD is replayed in order; ground truth per (record, commit)
is the actual exit status of the record's own command at that commit.
The stale axis involves no labeling agent at all.

    .venv/bin/python evals/freshness_realworld/run.py --repos-dir <dir> [--repo NAME]

Writes results/<repo>.json rows per (record, commit) point plus a setup
block. Scoring/aggregation is report.py's job.
"""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 - the measurement IS running repo test suites
import sys
import time
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from provalume.freshness.blast_radius import record_blast_radius  # noqa: E402
from provalume.freshness.executor import reverify_record  # noqa: E402
from provalume.freshness.watcher import process_landed_commit  # noqa: E402
from provalume.schemas.freshness import BlastRadiusMethod  # noqa: E402
from provalume.schemas.memories import MemoryType  # noqa: E402
from provalume.sdk.client import Provalume  # noqa: E402

#: Locked parameters (D21). Changing any of these invalidates comparisons.
N_BACK = 60
MAX_RECORDS = 10
COMMAND_TIMEOUT_S = 120
BUDGET_S_PER_REPO = 25 * 60


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed argv, scratch clone
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _run_command(command: str, repo: Path) -> str:
    """The record's command, honestly: pass | fail | error."""
    import os
    import shlex

    try:
        completed = subprocess.run(  # noqa: S603 - measurement target command
            shlex.split(command),
            cwd=repo,
            capture_output=True,
            timeout=COMMAND_TIMEOUT_S,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except subprocess.TimeoutExpired:
        return "error"
    if completed.returncode == 0:
        return "pass"
    if completed.returncode < 0:
        return "error"
    return "fail"


def _checkout(repo: Path, sha: str) -> None:
    _git(repo, "checkout", "-q", "--detach", sha)
    # Kill __pycache__ and build artifacts between commits: checkout stamps
    # changed files with "now", so the (mtime-seconds, size) bytecode cache
    # rule (D18) applies across commits exactly as it did within a case.
    _git(repo, "clean", "-qfdx", "-e", ".provalume")


def _test_files(repo: Path) -> list[str]:
    files = sorted(
        f
        for f in _git(repo, "ls-files").splitlines()
        if f.rpartition("/")[2].startswith("test_")
        and f.endswith(".py")
        and "bench" not in f
        and "extra" not in f
    )
    if len(files) <= MAX_RECORDS:
        return files
    stride = len(files) / MAX_RECORDS
    return [files[int(i * stride)] for i in range(MAX_RECORDS)]


def measure_repo(name: str, repo: Path, python: Path, out_dir: Path) -> None:
    started = time.monotonic()
    head = _git(repo, "rev-parse", "HEAD")
    chain = _git(repo, "rev-list", "--first-parent", f"HEAD~{N_BACK}..HEAD").splitlines()
    chain.reverse()  # oldest first
    base = _git(repo, "rev-parse", f"HEAD~{N_BACK}")

    print(f"[{name}] N={base[:12]} replaying {len(chain)} first-parent commits")
    _checkout(repo, base)

    # -- records at N -------------------------------------------------------
    pv = Provalume.open(repo / ".provalume" / "db.sqlite", project_id=name, root=repo)
    setup: dict[str, object] = {"repo": name, "base": base, "head": head}
    records: dict[str, str] = {}  # record_id -> command
    command_times: list[float] = []
    coverage_arm: dict[str, str] = {}
    try:
        for index, test_file in enumerate(_test_files(repo)):
            command = f"{python} -m pytest -q {test_file}"
            t0 = time.monotonic()
            verdict = _run_command(command, repo)
            command_times.append(time.monotonic() - t0)
            if verdict != "pass":
                print(f"[{name}]   skip (not green at N): {test_file} -> {verdict}")
                continue
            pv.record_verification(command=command, passed=True)
            record_id = next(
                m.memory_id
                for m in pv.memory_records(limit=50)
                if m.memory_type is MemoryType.PROCEDURAL
                and m.content.get("raw_command") == command
            )
            records[record_id] = command
            # Even-indexed records get a coverage radius so real history can
            # answer the radius-method question the synthetic corpus could
            # not (D21). Fail-open: extraction failure keeps the automatic
            # import_graph/commit_touch radius.
            if index % 2 == 0:
                produced = record_blast_radius(
                    pv,
                    record_id=record_id,
                    command=command,
                    method=BlastRadiusMethod.COVERAGE,
                    root=repo,
                    timeout_s=COMMAND_TIMEOUT_S,
                )
                coverage_arm[record_id] = "coverage" if produced is not None else "failed"

        radius_methods: dict[str, str | None] = {}
        for record_id in records:
            radii = [
                e
                for e in pv.events()
                if e.event_type.value == "blast_radius.recorded"
                and str(e.payload.get("record_id")) == record_id
            ]
            radius_methods[record_id] = str(radii[-1].payload.get("method")) if radii else None
        setup["records"] = {
            rid: {
                "command": cmd,
                "radius_method": radius_methods.get(rid),
                "coverage_arm": coverage_arm.get(rid),
            }
            for rid, cmd in records.items()
        }
        if not records:
            print(f"[{name}] no green records at N; repo dropped")
            (out_dir / f"{name}.json").write_text(
                json.dumps({"setup": setup, "rows": []}, indent=2) + "\n"
            )
            return

        # -- sampling (D21): stride so projected cost fits the budget -------
        mean_s = sum(command_times) / len(command_times)
        projected = len(chain) * len(records) * mean_s * 2
        stride = 1
        if projected > BUDGET_S_PER_REPO:
            stride = int(projected // BUDGET_S_PER_REPO) + 1
        sampled = chain[::stride]
        setup["sampling"] = {
            "chain_length": len(chain),
            "stride": stride,
            "sampled_commits": len(sampled),
            "mean_command_s": round(mean_s, 2),
            "projected_full_s": int(projected),
        }
        print(
            f"[{name}] {len(records)} records, mean cmd {mean_s:.1f}s, "
            f"stride {stride} -> {len(sampled)} commits"
        )

        # -- replay ----------------------------------------------------------
        rows: list[dict[str, object]] = []
        for order, sha in enumerate(sampled):
            _checkout(repo, sha)
            scan = process_landed_commit(pv, commit_sha=sha)
            fresh_suspects = {str(e.payload.get("record_id")) for e in scan.triggered}
            reasons = {
                str(e.payload.get("record_id")): str(e.payload.get("reason_code"))
                for e in scan.assessed
            }
            for record_id, command in records.items():
                memory = pv.memories.get(record_id)
                state_after_scan = memory.freshness.value if memory else ""
                rerun_outcome = None
                if state_after_scan in {"suspect", "stale"}:
                    rerun = reverify_record(
                        pv,
                        record_id=record_id,
                        trigger_commit=sha,
                        allowlist=("*",),  # harness-only; shipped default off
                        timeout_s=COMMAND_TIMEOUT_S,
                        root=repo,
                    )
                    rerun_outcome = None if rerun is None else str(rerun.payload.get("outcome"))
                truth = _run_command(command, repo)
                memory = pv.memories.get(record_id)
                rows.append(
                    {
                        "commit": sha,
                        "order": order,
                        "record_id": record_id,
                        "truth": truth,
                        "state_after_scan": state_after_scan,
                        "fresh_suspect": record_id in fresh_suspects,
                        "reason_code": reasons.get(record_id),
                        "rerun_outcome": rerun_outcome,
                        "state_final": memory.freshness.value if memory else "",
                    }
                )
            if order % 10 == 0:
                print(f"[{name}]   commit {order + 1}/{len(sampled)}")

        setup["wall_clock_s"] = int(time.monotonic() - started)
        (out_dir / f"{name}.json").write_text(
            json.dumps({"setup": setup, "rows": rows}, indent=2) + "\n"
        )
        print(f"[{name}] done: {len(rows)} points in {setup['wall_clock_s']}s")
    finally:
        pv.close()
        _checkout(repo, head)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repos-dir", required=True)
    parser.add_argument("--python", required=True, help="harness venv python for commands")
    parser.add_argument("--repo", action="append", help="repo dir name(s); default all")
    args = parser.parse_args()
    repos_dir = Path(args.repos_dir)
    out_dir = EVAL_DIR / "results"
    out_dir.mkdir(exist_ok=True)
    names = args.repo or ["boltons", "more-itertools", "jmespath", "toolz"]
    for name in names:
        measure_repo(name, repos_dir / name, Path(args.python), out_dir)
