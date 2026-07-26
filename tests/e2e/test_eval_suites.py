"""The eval CLI's suite selection, end to end."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tests.e2e.conftest import CliRunner

FIXTURES = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "trajectories"


def test_trajectory_suite_from_cli(cli: CliRunner, tmp_path: Path) -> None:
    result = cli(
        "eval",
        "--suite",
        "trajectories",
        "--fixtures",
        str(FIXTURES),
        "--json",
        cwd=tmp_path,
    )
    payload = json.loads(result.stdout)
    assert payload["passed"] is True
    assert payload["total"] >= 4
    assert "trajectories" in payload
    assert payload["metrics"]["occurrence_fidelity"]["denominator"] > 0


def test_trajectory_suite_single_fixture(cli: CliRunner, tmp_path: Path) -> None:
    result = cli(
        "eval",
        "--suite",
        "trajectories",
        "--scenario",
        "repeat-blocked",
        "--fixtures",
        str(FIXTURES),
        "--json",
        cwd=tmp_path,
    )
    payload = json.loads(result.stdout)
    assert payload["total"] == 1
    assert payload["trajectories"][0]["name"] == "repeat-blocked"


def test_unknown_suite_is_refused(cli: CliRunner, tmp_path: Path) -> None:
    result = cli("eval", "--suite", "nonsense", cwd=tmp_path, expect=2)
    assert "unknown suite" in result.stdout + result.stderr
