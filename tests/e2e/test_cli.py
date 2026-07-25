"""CLI behaviour, including the --json output contract (ADR-0017).

The `--json` shapes are a stability contract: integrations parse them, so a
changed key is a breaking change even though no Python signature moved.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - invokes the CLI under test
import sys
from pathlib import Path

import pytest

CLI = [sys.executable, "-m", "provalume.cli.main"]


def run(*args: str, cwd: Path, expect: int | None = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603 - fixed argv, test-controlled cwd
        [*CLI, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "NO_COLOR": "1", "HOME": str(cwd)},
    )
    if expect is not None:
        assert result.returncode == expect, (
            f"expected exit {expect}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.fixture
def project(tmp_path: Path) -> Path:
    run("init", "--project", "cli-test", cwd=tmp_path)
    return tmp_path


# --- Basics ----------------------------------------------------------------


def test_version_flag(tmp_path: Path) -> None:
    result = run("--version", cwd=tmp_path)
    assert "provalume" in result.stdout


def test_help_lists_the_documented_commands(tmp_path: Path) -> None:
    result = run("--help", cwd=tmp_path)
    for command in (
        "init", "doctor", "status", "demo", "events", "memories", "recall",
        "explain", "preflight", "propose", "promote", "invalidate", "supersede",
        "export", "import", "rebuild", "audit", "replay", "eval", "serve-mcp",
    ):
        assert command in result.stdout, f"{command} is missing from --help"


def test_init_creates_the_database(tmp_path: Path) -> None:
    run("init", "--project", "p", cwd=tmp_path)
    assert (tmp_path / ".provalume" / "provalume.db").exists()


def test_init_json_shape(tmp_path: Path) -> None:
    result = run("init", "--project", "p", "--json", cwd=tmp_path)
    payload = json.loads(result.stdout)
    for key in ("project_id", "database", "schema_version", "events", "chain_head"):
        assert key in payload, f"init --json lost the {key!r} key"


def test_doctor_reports_checks(project: Path) -> None:
    result = run("doctor", "--json", cwd=project)
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    names = {c["check"] for c in payload["checks"]}
    assert {"python", "sqlite", "fts5", "database", "integrity"} <= names


def test_status_json_shape(project: Path) -> None:
    payload = json.loads(run("status", "--json", cwd=project).stdout)
    for key in ("project_id", "events", "chain_head", "memories_by_trust",
                "memories_by_type", "schema_version"):
        assert key in payload


# --- Round trip through the CLI --------------------------------------------


def test_propose_lands_quarantined(project: Path) -> None:
    payload = json.loads(
        run("propose", "the project uses uv", "--json", cwd=project).stdout
    )
    assert payload["trust_state"] == "quarantined"

    memories = json.loads(run("memories", "--json", cwd=project).stdout)
    assert memories
    assert all(m["trust_state"] == "quarantined" for m in memories)


def test_promotion_without_evidence_is_refused(project: Path) -> None:
    run("propose", "an unsupported claim", cwd=project)
    memories = json.loads(run("memories", "--json", cwd=project).stdout)
    memory_id = memories[0]["memory_id"]

    result = run("promote", memory_id, "--to", "verified", "--json",
                 cwd=project, expect=1)
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["rule"], "a refusal must name the rule that refused it"


def test_recall_and_explain_round_trip(project: Path) -> None:
    run("propose", "integration tests are slow under parallelism", cwd=project)
    results = json.loads(
        run("recall", "integration parallelism", "--trust", "quarantined",
            "--json", cwd=project).stdout
    )
    assert results
    memory_id = results[0]["memory_id"]

    provenance = json.loads(run("explain", memory_id, "--json", cwd=project).stdout)
    assert provenance["memory_id"] == memory_id
    assert "resolution" in provenance
    assert "trust_state" in provenance


def test_recall_digest_is_banner_first(project: Path) -> None:
    run("propose", "a proposed fact about the build", cwd=project)
    result = run("recall", "build", "--trust", "quarantined", "--digest", "1500",
                 cwd=project)
    assert result.stdout.startswith("Historical context from Provalume follows.")


def test_recall_json_result_shape(project: Path) -> None:
    run("propose", "a fact", cwd=project)
    results = json.loads(
        run("recall", "fact", "--trust", "quarantined", "--json", cwd=project).stdout
    )
    assert results
    for key in ("memory_id", "memory_type", "text", "trust_state", "score",
                "rank", "explanation", "presentable_as_current_truth"):
        assert key in results[0], f"recall --json lost the {key!r} key"


def test_preflight_exits_zero_whether_or_not_it_warns(project: Path) -> None:
    """An exit code meaning "blocked" would make it a gate scripts route around."""
    payload = json.loads(
        run("preflight", "--command", "pytest -q", "--json", cwd=project).stdout
    )
    assert payload["matched"] is False


def test_export_import_round_trip(project: Path, tmp_path: Path) -> None:
    run("propose", "an exportable fact", cwd=project)

    out = tmp_path / "export"
    exported = json.loads(run("export", "--out", str(out), "--json", cwd=project).stdout)
    assert exported["memories"] >= 1
    assert (out / "events.jsonl").exists()

    imported = json.loads(run("import", str(out), "--json", cwd=project).stdout)
    assert imported["ok"] is True
    assert imported["duplicates"] >= 1, "re-import should deduplicate"


def test_export_is_byte_identical_across_runs(project: Path, tmp_path: Path) -> None:
    run("propose", "a deterministic fact", cwd=project)
    first, second = tmp_path / "a", tmp_path / "b"
    run("export", "--out", str(first), cwd=project)
    run("export", "--out", str(second), cwd=project)
    for name in ("events.jsonl", "memories.jsonl", "transitions.jsonl"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_rebuild_reports_statistics(project: Path) -> None:
    run("propose", "a fact to rebuild", cwd=project)
    stats = json.loads(run("rebuild", "--json", cwd=project).stdout)
    assert stats["events_processed"] >= 1
    assert "memories_written" in stats


def test_audit_passes_and_reports_the_chain_head(project: Path) -> None:
    run("propose", "a fact", cwd=project)
    payload = json.loads(run("audit", "--json", cwd=project).stdout)
    assert payload["ok"] is True
    assert payload["chain_head"].startswith("sha256:")
    assert payload["findings"]


def test_audit_strict_still_passes_on_a_clean_database(project: Path) -> None:
    run("propose", "a fact", cwd=project)
    run("audit", "--strict", cwd=project)


def test_events_json_shape(project: Path) -> None:
    run("propose", "a fact", cwd=project)
    events = json.loads(run("events", "--json", cwd=project).stdout)
    assert events
    for key in ("event_id", "event_type", "recorded_at", "project_id", "source",
                "payload_hash"):
        assert key in events[0]


# --- Demo, eval, MCP -------------------------------------------------------


def test_demo_runs_offline(tmp_path: Path) -> None:
    result = run("demo", cwd=tmp_path)
    assert "Provalume demo" in result.stdout
    assert "Historical context from Provalume follows." in result.stdout
    assert "Done" in result.stdout


def test_demo_writes_a_light_themed_html_report(tmp_path: Path) -> None:
    out = tmp_path / "report.html"
    run("demo", "--html", str(out), cwd=tmp_path)
    html = out.read_text()
    assert "<!doctype html>" in html.lower()
    # Light-first identity: warm white background, never a dark default.
    assert "#FCFAF5" in html or "--pv-warm-white" in html
    assert "Historical context from Provalume follows." in html


def test_eval_runs_every_scenario(tmp_path: Path) -> None:
    payload = json.loads(run("eval", "--json", cwd=tmp_path).stdout)
    assert payload["total"] == 20
    assert payload["passed"] is True, [
        s for s in payload["scenarios"] if not s["passed"]
    ]
    assert "No comparison against another system" in payload["note"]


def test_eval_single_scenario(tmp_path: Path) -> None:
    payload = json.loads(run("eval", "--scenario", "poisoning", "--json", cwd=tmp_path).stdout)
    assert payload["total"] == 1
    assert payload["scenarios"][0]["name"] == "poisoning"


def test_poisoning_success_rate_is_zero(tmp_path: Path) -> None:
    """Target zero. A non-zero result is a bug, not a tuning parameter."""
    payload = json.loads(run("eval", "--json", cwd=tmp_path).stdout)
    poisoning = payload["metrics"]["poisoning_success"]
    assert poisoning["denominator"] > 0, "the poisoning scenario did not run"
    assert poisoning["numerator"] == 0, "an adversarial record escaped quarantine"


def test_serve_mcp_help(tmp_path: Path) -> None:
    result = run("serve-mcp", "--help", cwd=tmp_path)
    assert "--read-only" in result.stdout
    assert "rate-limit" in result.stdout


def test_no_color_output_is_plain(project: Path) -> None:
    result = run("status", cwd=project)
    assert "\x1b[" not in result.stdout, "NO_COLOR was not honoured"
