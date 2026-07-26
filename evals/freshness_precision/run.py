"""The M5 harness: run every corpus case through the real freshness path.

For each case: build the repository, prove the seed state actually passes
its command (a corpus bug must not masquerade as measurement), record the
verification through the real SDK path, land the case's commit, scan it
(`process_landed_commit`), then re-execute (`reverify_record`, allowlist
"*" — harness-only; the shipped default stays off). Results are written as
data; scoring against the independent labels is `score.py`'s job.

    .venv/bin/python evals/freshness_precision/run.py            # measure
    .venv/bin/python evals/freshness_precision/run.py --export   # labeling packet

The labeling packet contains ONLY the claim, the command, and the pre/post
file contents — no system output, no reason codes, nothing the labeler
could use to reverse-engineer what the implementation decided (D17).
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess  # nosec B404 - builds throwaway corpus repositories
import sys
import tempfile
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(EVAL_DIR))

from corpus import CASES, Case  # noqa: E402

from provalume.freshness.executor import reverify_record  # noqa: E402
from provalume.freshness.watcher import process_landed_commit  # noqa: E402
from provalume.schemas.memories import MemoryType  # noqa: E402
from provalume.sdk.client import Provalume  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed argv, throwaway directory
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit_all(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=m5@eval", "-c", "user.name=m5", "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


def _materialize(repo: Path, files: dict[str, str | None]) -> None:
    for path, content in files.items():
        target = repo / path
        if content is None:
            target.unlink(missing_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def _command(case: Case) -> str:
    return case.command.replace("{python}", sys.executable)


def _run_case(case: Case, workdir: Path) -> dict[str, object]:
    repo = workdir / case.case_id
    repo.mkdir()
    # Scaffolding, not case content: .provalume/ is gitignored by Provalume
    # convention, and without this the harness's own `git add -A` would
    # track the database — whose journal writes would then read as a dirty
    # worktree to the executor's gate and refuse every re-run.
    (repo / ".gitignore").write_text(".provalume/\n__pycache__/\n")
    _materialize(repo, dict(case.files_before))
    _git(workdir, "init", "-q", "-b", "main", str(repo))
    _commit_all(repo, "seed")

    command = _command(case)
    # PYTHONDONTWRITEBYTECODE: this scaffolding run must leave no
    # __pycache__ behind. CPython validates bytecode by (mtime-seconds,
    # size), and the harness lands the after-state within the same second —
    # a same-size edit would then re-serve the SEED state's bytecode to the
    # measured re-run, producing a false pass that is the harness's fault,
    # not the system's. (First observed live: four should-invalidate cases
    # flipped between runs on exactly this race.)
    seed = subprocess.run(  # noqa: S603 - the corpus's own command, throwaway repo
        shlex.split(command),
        cwd=repo,
        capture_output=True,
        timeout=60,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if seed.returncode != 0:
        msg = f"corpus bug: seed state of {case.case_id} does not pass its command"
        raise RuntimeError(msg)

    pv = Provalume.open(repo / ".provalume" / "db.sqlite", project_id="m5", root=repo)
    try:
        pv.record_verification(command=command, passed=True)
        record_id = next(
            m.memory_id
            for m in pv.memory_records(limit=10)
            if m.memory_type is MemoryType.PROCEDURAL
        )
        memory = pv.memories.get(record_id)
        if memory is None:
            raise RuntimeError(f"corpus bug: no record for {case.case_id}")
        initial = memory.freshness.value

        radius_events = [
            e
            for e in pv.events()
            if e.event_type.value == "blast_radius.recorded"
            and str(e.payload.get("record_id")) == record_id
        ]
        radius_method = str(radius_events[-1].payload.get("method")) if radius_events else None
        radius_path_count = len(radius_events[-1].payload.get("paths", [])) if radius_events else 0

        sha = ""
        after_scan = initial
        reason_code = None
        intersecting_count = 0
        if case.files_after:
            _materialize(repo, dict(case.files_after))
            sha = _commit_all(repo, f"land {case.case_id}")
            scan = process_landed_commit(pv, commit_sha=sha)
            memory = pv.memories.get(record_id)
            if memory is None:
                raise RuntimeError(f"record vanished mid-case: {case.case_id}")
            after_scan = memory.freshness.value
            verdicts = [e for e in scan.assessed if str(e.payload.get("record_id")) == record_id]
            if verdicts:
                reason_code = str(verdicts[-1].payload.get("reason_code"))
            triggers = [e for e in scan.triggered if str(e.payload.get("record_id")) == record_id]
            if triggers:
                intersecting_count = len(triggers[-1].payload.get("intersecting_paths", []))

        rerun = reverify_record(
            pv,
            record_id=record_id,
            trigger_commit=sha,
            allowlist=("*",),  # harness-only; the shipped default is off (T27)
            timeout_s=60.0,
            root=repo,
        )
        memory = pv.memories.get(record_id)
        if memory is None:
            raise RuntimeError(f"record vanished mid-case: {case.case_id}")
        return {
            "case_id": case.case_id,
            "category": case.category,
            "initial_freshness": initial,
            "freshness_after_scan": after_scan,
            "reason_code": reason_code,
            "rerun_outcome": None if rerun is None else str(rerun.payload.get("outcome")),
            "freshness_final": memory.freshness.value,
            # Diagnostics (task: false-suspect cross-tabulation). Extra
            # fields; score.py ignores them.
            "radius_method": radius_method,
            "radius_path_count": radius_path_count,
            "intersecting_path_count": intersecting_count,
            "record_type": memory.memory_type.value,
        }
    finally:
        pv.close()


def measure() -> None:
    results = []
    with tempfile.TemporaryDirectory(prefix="m5-freshness-") as tmp:
        workdir = Path(tmp)
        for case in CASES:
            results.append(_run_case(case, workdir))
            print(
                f"  {case.case_id}: scan={results[-1]['freshness_after_scan']}"
                f" rerun={results[-1]['rerun_outcome']}"
                f" final={results[-1]['freshness_final']}"
            )
    out = EVAL_DIR / "results" / "results.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=2) + "\n")
    print(f"\n{len(results)} cases -> {out}")


def export_labeling_packet() -> None:
    packet = [
        {
            "case_id": case.case_id,
            "claim": (
                "A verified record claims: running the command below from the "
                "repository root succeeds (exit code 0)."
            ),
            "command": case.command,
            "files_before": case.files_before,
            "files_after": case.files_after,
        }
        for case in CASES
    ]
    out = EVAL_DIR / "labeling" / "cases.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(packet, indent=2) + "\n")
    shutil.copy(EVAL_DIR / "LABELING.md", out.parent / "LABELING.md")
    print(f"{len(packet)} cases -> {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", action="store_true", help="emit the labeling packet")
    args = parser.parse_args()
    if args.export:
        export_labeling_packet()
    else:
        measure()
