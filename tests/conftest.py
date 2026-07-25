"""Shared fixtures.

Tests use in-memory databases by default: fast, isolated, and exercising the same
code as a file database except for WAL, which has its own tests in
``tests/integration/test_concurrency.py``.
"""

from __future__ import annotations

import subprocess  # nosec B404 - creates throwaway test repositories
from collections.abc import Iterator
from pathlib import Path

import pytest

from provalume.sdk.client import Provalume
from provalume.store.db import Database, open_database
from provalume.store.journal import Journal
from provalume.store.projections import Projector
from provalume.store.repository import MemoryRepository


@pytest.fixture
def db() -> Iterator[Database]:
    database = open_database(":memory:")
    yield database
    database.close()


@pytest.fixture
def file_db(tmp_path: Path) -> Iterator[Database]:
    """A real on-disk database, for anything that needs WAL or durability."""
    database = open_database(tmp_path / "provalume.db")
    yield database
    database.close()


@pytest.fixture
def journal(db: Database) -> Journal:
    return Journal(db)


@pytest.fixture
def repository(db: Database) -> MemoryRepository:
    return MemoryRepository(db)


@pytest.fixture
def projector(journal: Journal, repository: MemoryRepository) -> Projector:
    return Projector(journal, repository)


@pytest.fixture
def pv(db: Database) -> Provalume:
    """A Provalume instance with Git disabled, for deterministic tests."""
    return Provalume(db, project_id="test-project", git=None)


@pytest.fixture
def pv_file(file_db: Database) -> Provalume:
    return Provalume(file_db, project_id="test-project", git=None)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A throwaway Git repository with three commits on a linear history."""

    def git(*args: str) -> str:
        return subprocess.run(  # noqa: S603 - fixed argv, throwaway directory
            ["git", "-C", str(tmp_path), *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "test@provalume.invalid")
    git("config", "user.name", "Test")
    git("config", "commit.gpgsign", "false")
    for index in range(3):
        (tmp_path / f"file{index}.txt").write_text(f"content {index}\n")
        git("add", "-A")
        git("commit", "-qm", f"commit {index}")
    return tmp_path


@pytest.fixture
def git_commits(git_repo: Path) -> list[str]:
    """The three commit SHAs, oldest first."""
    output = subprocess.run(  # noqa: S603 - fixed argv
        ["git", "-C", str(git_repo), "log", "--format=%H", "--reverse"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout
    return output.split()
