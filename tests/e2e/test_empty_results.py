"""An empty listing must say why it is empty.

An empty result has two very different causes — the database is empty, or the
query is aimed at a project it does not hold — and printed as nothing they are
indistinguishable. The second reads as "the integration recorded nothing", which
is both wrong and expensive to chase. This was found by dogfooding, after a long
misdiagnosis of exactly that kind.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from tests.e2e.conftest import CliRunner


@pytest.fixture
def db_holding_dogfood(tmp_path: Path) -> Path:
    """A database containing one record under the project id ``dogfood``."""
    from provalume import Provalume

    db = tmp_path / "memory.db"
    pv = Provalume.open(db, project_id="dogfood", use_git=False)
    pv.record_verification(command="pytest -q", passed=False, excerpt="AssertionError: nope")
    pv.close()
    return db


def test_an_empty_database_says_it_is_empty(tmp_path: Path, cli: CliRunner) -> None:
    result = cli("events", "--db", str(tmp_path / "fresh.db"), "--project", "any", cwd=tmp_path)

    assert "empty" in result.stdout.lower()


def test_a_project_mismatch_names_the_projects_that_do_exist(
    tmp_path: Path, db_holding_dogfood: Path, cli: CliRunner
) -> None:
    result = cli("events", "--db", str(db_holding_dogfood), "--project", "wrong-name", cwd=tmp_path)

    # The three things a reader needs: what was asked for, what is actually
    # there, and how to fix it.
    assert "wrong-name" in result.stdout
    assert "dogfood" in result.stdout
    assert "--project" in result.stdout


@pytest.mark.parametrize("command", [["memories"], ["recall", "anything"]])
def test_memories_and_recall_explain_a_mismatch_too(
    tmp_path: Path, db_holding_dogfood: Path, command: list[str], cli: CliRunner
) -> None:
    result = cli(*command, "--db", str(db_holding_dogfood), "--project", "wrong-name", cwd=tmp_path)

    assert "dogfood" in result.stdout


def test_a_real_match_still_lists_normally(
    tmp_path: Path, db_holding_dogfood: Path, cli: CliRunner
) -> None:
    result = cli("events", "--db", str(db_holding_dogfood), "--project", "dogfood", cwd=tmp_path)

    assert "verification.failed" in result.stdout
    assert "empty" not in result.stdout.lower()
