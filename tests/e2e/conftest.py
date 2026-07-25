"""Shared helpers for the end-to-end CLI tests.

The CLI runner lives here as a fixture rather than in a test module. Importing
across test modules (`from tests.e2e.test_cli import run`) only works when the
repository root happens to be on `sys.path`, which is true when running locally
from the root and false on CI — where it fails with `No module named 'tests'`.
A fixture is injected by pytest, so it needs no import path at all.
"""

from __future__ import annotations

import subprocess  # nosec B404 - invokes the CLI under test
import sys
from typing import TYPE_CHECKING, Protocol

import pytest

if TYPE_CHECKING:
    from pathlib import Path

CLI = [sys.executable, "-m", "provalume.cli.main"]


class CliRunner(Protocol):
    def __call__(
        self, *args: str, cwd: Path, expect: int | None = 0
    ) -> subprocess.CompletedProcess[str]:
        """Run the CLI and return the completed process."""


@pytest.fixture
def cli() -> CliRunner:
    """Run the CLI in a subprocess with a minimal, colour-free environment."""

    def run(*args: str, cwd: Path, expect: int | None = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(  # noqa: S603 - fixed argv, test-controlled cwd
            [*CLI, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env={
                "PATH": "/usr/bin:/bin:/usr/local/bin",
                "NO_COLOR": "1",
                "HOME": str(cwd),
            },
        )
        if expect is not None:
            assert result.returncode == expect, (
                f"{args} exited {result.returncode}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result

    return run
