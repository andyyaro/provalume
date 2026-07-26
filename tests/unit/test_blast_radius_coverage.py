"""``coverage`` blast-radius extraction.

This is the one extraction method that executes something, so the happy paths
here execute the real thing: real coverage.py, real subprocesses, real
throwaway projects under ``tmp_path``. A mocked coverage run would prove
nothing about the flags, the data-file isolation, or the shape of the report.

What the negative tests are careful about, in order of how badly a silent
regression would hurt:

- an unsupported command must run **nothing** (asserted with sentinels the
  command would have created, and by snapshotting the project tree);
- the verified command's own failure is not an extraction failure — a failing
  suite still has a radius;
- a timeout returns ``None`` promptly and leaves no process finishing behind
  our back;
- missing coverage.py returns ``None`` *without* having executed the command;
- the project's own ``.coverage`` is never written.
"""

from __future__ import annotations

import re
import subprocess  # nosec B404 - drives the real extractor and its controls
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from provalume.freshness import _coverage
from provalume.freshness.blast_radius import BlastRadius
from provalume.schemas.freshness import BlastRadiusMethod

#: A real coverage run costs seconds; the budget is generous so that a loaded
#: machine cannot turn a green test red. The timeout test uses its own.
BUDGET = 180.0

MODULE = """\
VALUE = 1


def classify(number):
    if number > 0:
        return "positive"
    return "non-positive"
"""

TEST_MODULE = """\
import mod


def test_value():
    assert mod.VALUE == 1


def test_classify():
    assert mod.classify(1) == "positive"
"""

#: Executed lines are exactly 1, 2, 6, 7: lines 3 to 5 are real statements
#: inside a branch that is never taken, so the two executed regions are
#: genuinely disjoint rather than separated by blank lines.
GAPPED_MODULE = """\
EXECUTED = 1
if EXECUTED > 100:
    SKIPPED_A = 2
    SKIPPED_B = 3
    SKIPPED_C = 4
EXECUTED_AGAIN = 5
DONE = EXECUTED + EXECUTED_AGAIN
"""


def _write_project(root: Path) -> Path:
    """A minimal real project: a module, a test importing it, and the empty
    root ``conftest.py`` that puts the root on ``sys.path`` under pytest's
    prepend import mode (so ``import mod`` resolves from ``tests/``)."""
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "mod.py").write_text(MODULE)
    (root / "conftest.py").write_text("")
    (root / "tests" / "test_mod.py").write_text(TEST_MODULE)
    return root


def _tree(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*")}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return _write_project(tmp_path / "proj")


@pytest.fixture(scope="module")
def happy(tmp_path_factory: pytest.TempPathFactory) -> tuple[BlastRadius, Path]:
    """One real ``pytest`` run under coverage, shared by the shape assertions.

    Acceptance 1: the happy path. Every property of the returned radius is
    then asserted separately against this single execution.
    """
    root = _write_project(tmp_path_factory.mktemp("happy") / "proj")
    radius = _coverage.extract(f"{sys.executable} -m pytest tests/", root, timeout_s=BUDGET)
    assert radius is not None, "the happy path must produce a radius"
    return radius, root


# --- happy path ---------------------------------------------------------------


def test_radius_covers_the_test_and_the_module_it_exercised(
    happy: tuple[BlastRadius, Path],
) -> None:
    radius, _ = happy
    assert radius.method is BlastRadiusMethod.COVERAGE
    assert radius.tool == "coverage.py"
    assert re.fullmatch(r"\d+\.\d+(\.\S+)?", radius.tool_version), radius.tool_version
    assert "mod.py" in radius.paths
    assert "tests/test_mod.py" in radius.paths


def test_paths_are_sorted_deduplicated_relative_posix_paths_under_root(
    happy: tuple[BlastRadius, Path],
) -> None:
    radius, root = happy
    assert radius.paths == tuple(sorted(set(radius.paths)))
    for path in radius.paths:
        assert not path.startswith("/"), f"{path} is not relative"
        assert "\\" not in path, f"{path} is not POSIX"
        assert ".." not in Path(path).parts
        assert (root / path).is_file(), f"{path} does not exist under the root"
    # Nothing from site-packages, the interpreter, or the stdlib leaked in.
    assert not any("site-packages" in path for path in radius.paths)


def test_line_range_keys_are_a_subset_of_paths(happy: tuple[BlastRadius, Path]) -> None:
    """Acceptance 8. A range keyed by a path the radius does not claim would
    be evidence about a file nobody said was involved."""
    radius, _ = happy
    assert radius.line_ranges is not None
    assert set(radius.line_ranges) <= set(radius.paths)


def test_line_ranges_are_inclusive_sorted_and_within_the_files(
    happy: tuple[BlastRadius, Path],
) -> None:
    radius, root = happy
    assert radius.line_ranges is not None
    for path, ranges in radius.line_ranges.items():
        total = len((root / path).read_text().splitlines())
        assert ranges, f"{path} was listed with no executed lines"
        assert ranges == tuple(sorted(ranges))
        previous_end: int | None = None
        for start, end in ranges:
            assert 1 <= start <= end <= total, f"{path}: implausible range {(start, end)}"
            if previous_end is not None:
                # Non-overlapping, and never adjacent — (1, 2) and (3, 4)
                # would have been compressed into (1, 4).
                assert start > previous_end + 1, f"{path}: {ranges} is not compressed"
            previous_end = end


def test_the_projects_own_coverage_file_is_never_written(
    happy: tuple[BlastRadius, Path],
) -> None:
    """T27 posture: the data file is a tempfile, so extraction leaves no
    coverage artefact in the operator's repository."""
    radius, root = happy
    residue = sorted(
        path.name
        for path in root.iterdir()
        if path.name.startswith(".coverage") or path.name == "coverage.json"
    )
    assert residue == []
    assert radius.paths  # the run really happened, so the absence means something


# --- the command's own failure is not an extraction failure --------------------


def test_a_failing_command_still_has_a_radius(project: Path) -> None:
    """Acceptance 2. A failing test run executed code, and what it executed is
    exactly what a blast radius is for."""
    (project / "tests" / "test_fail.py").write_text("def test_bad():\n    assert False\n")
    control = subprocess.run(  # fixed argv, shell=False, throwaway directory
        [sys.executable, "-m", "pytest", "tests/test_fail.py", "-q"],
        cwd=project,
        capture_output=True,
        timeout=BUDGET,
        check=False,
    )
    assert control.returncode != 0, "positive control: the command must really fail"

    radius = _coverage.extract(
        f"{sys.executable} -m pytest tests/test_fail.py", project, timeout_s=BUDGET
    )
    assert radius is not None
    assert "tests/test_fail.py" in radius.paths


# --- command forms ------------------------------------------------------------


def test_bare_console_entry_is_rewritten_to_a_module_run(project: Path) -> None:
    """Acceptance 3. ``pytest tests/`` names no interpreter, so the running
    one stands in."""
    radius = _coverage.extract("pytest tests/", project, timeout_s=BUDGET)
    assert radius is not None
    assert "mod.py" in radius.paths
    assert "tests/test_mod.py" in radius.paths


def test_script_form_compresses_disjoint_regions_into_two_ranges(tmp_path: Path) -> None:
    """Acceptance 7, plus the ``python script.py`` form. Lines 1-2 and 6-7 ran,
    3-5 did not, so the compression must produce two ranges and not one."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "gaps.py").write_text(GAPPED_MODULE)

    radius = _coverage.extract(f"{sys.executable} gaps.py", root, timeout_s=BUDGET)
    assert radius is not None
    assert radius.paths == ("gaps.py",)
    assert radius.line_ranges == {"gaps.py": ((1, 2), (6, 7))}


# --- unsupported commands execute nothing -------------------------------------


def test_non_python_command_returns_none_without_executing_anything(project: Path) -> None:
    """Acceptance 4. The sentinel is the whole point: ``None`` is only the
    right answer if the command never ran."""
    before = _tree(project)
    assert _coverage.extract("make build", project, timeout_s=BUDGET) is None
    assert _coverage.extract("touch sentinel", project, timeout_s=BUDGET) is None
    assert _coverage.extract("sh -c 'touch sentinel'", project, timeout_s=BUDGET) is None
    assert not (project / "sentinel").exists(), "an unsupported command was executed"
    assert _tree(project) == before, "an unsupported command changed the project tree"


def test_unsupported_python_forms_return_none_without_executing(project: Path) -> None:
    """``-c`` is a Python invocation but not one of the three supported forms,
    and guessing is what this method refuses to do."""
    before = _tree(project)
    commands = [
        f"{sys.executable} -c \"open('sentinel', 'w').write('x')\"",
        f"{sys.executable} -u -m pytest tests/",
        f"{sys.executable} -m",
        f"{sys.executable} -m -x",
        f"{sys.executable}",
        f"{sys.executable} does_not_exist.py",
        "pytest-cov tests/",
        "",
        "   ",
        "'unterminated quote",
    ]
    for command in commands:
        assert _coverage.extract(command, project, timeout_s=BUDGET) is None, command
    assert not (project / "sentinel").exists()
    assert _tree(project) == before


def test_a_script_outside_the_root_is_never_executed(project: Path, tmp_path: Path) -> None:
    """A script that cannot contribute a path under the root has no radius,
    and executing it would cross a boundary this method has no business
    crossing."""
    outside = tmp_path / "outside.py"
    outside.write_text(f"open({str(tmp_path / 'sentinel')!r}, 'w').write('x')\n")

    assert _coverage.extract(f"{sys.executable} {outside}", project, timeout_s=BUDGET) is None
    assert not (tmp_path / "sentinel").exists(), "a script outside the root was executed"


# --- failure modes: every one of them fails open ------------------------------


def test_timeout_returns_none_and_does_not_hang(project: Path) -> None:
    """Acceptance 5. The command is killed at the deadline: it started, and it
    never reached its end."""
    (project / "sleeper.py").write_text(
        "import pathlib\nimport time\n\n"
        "pathlib.Path('started').write_text('x')\n"
        "time.sleep(120)\n"
        "pathlib.Path('finished').write_text('x')\n"
    )
    started = time.monotonic()
    radius = _coverage.extract(f"{sys.executable} sleeper.py", project, timeout_s=5.0)
    elapsed = time.monotonic() - started

    assert radius is None
    assert elapsed < 60, f"extraction did not honour the timeout ({elapsed:.1f}s)"
    assert (project / "started").exists(), "positive control: the command must have run"
    assert not (project / "finished").exists(), "the timed-out command was left running"


def test_missing_coverage_returns_none_and_never_runs_the_command(
    project: Path, tmp_path: Path
) -> None:
    """Acceptance 6a. An interpreter without coverage.py is probed once and
    the operator's command is never executed."""
    log = tmp_path / "invocations.log"
    shim = tmp_path / "bin" / "python3"
    shim.parent.mkdir()
    shim.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {str(log)!r}\nexit 1\n')
    shim.chmod(0o755)

    assert _coverage.extract(f"{shim} -m pytest tests/", project, timeout_s=BUDGET) is None
    assert log.read_text().splitlines() == ["-m coverage --version"], (
        "the tooling probe must come first, and nothing may run after it fails"
    )


def test_exploding_subprocess_fails_open(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Acceptance 6b. I5: return ``None``, raise nothing."""

    def explode(*args: object, **kwargs: object) -> None:
        raise OSError("injected: no subprocess available")

    monkeypatch.setattr(subprocess, "run", explode)
    assert (
        _coverage.extract(f"{sys.executable} -m pytest tests/", project, timeout_s=BUDGET) is None
    )


def test_unparseable_report_fails_open(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Acceptance 6c. Coverage tooling that runs but reports garbage is a
    tooling failure, not a radius."""
    real_run = subprocess.run

    def broken_report(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "json" in argv:
            return subprocess.CompletedProcess(argv, 0, "{not json at all", "")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", broken_report)
    assert (
        _coverage.extract(f"{sys.executable} -m pytest tests/", project, timeout_s=BUDGET) is None
    )


def test_failed_report_command_fails_open(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-zero exit from ``coverage json`` is the tooling failing, which is
    the one non-zero exit code this method does treat as fatal."""
    real_run = subprocess.run

    def failed_report(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "json" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "No data to report.")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", failed_report)
    assert (
        _coverage.extract(f"{sys.executable} -m pytest tests/", project, timeout_s=BUDGET) is None
    )


def test_non_positive_timeout_executes_nothing(project: Path) -> None:
    before = _tree(project)
    assert _coverage.extract(f"{sys.executable} -m pytest tests/", project, timeout_s=0.0) is None
    assert _coverage.extract(f"{sys.executable} -m pytest tests/", project, timeout_s=-1.0) is None
    assert _tree(project) == before


def test_a_radius_over_the_path_cap_is_not_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``MAX_RADIUS_PATHS``: over the cap the method fails rather than
    truncating, so the next method gets its turn."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "helper.py").write_text("HELPED = 1\n")
    (root / "main.py").write_text("import helper\n\nUSED = helper.HELPED\n")
    command = f"{sys.executable} main.py"

    control = _coverage.extract(command, root, timeout_s=BUDGET)
    assert control is not None
    assert control.paths == ("helper.py", "main.py")

    monkeypatch.setattr(_coverage, "MAX_RADIUS_PATHS", 1)
    assert _coverage.extract(command, root, timeout_s=BUDGET) is None


def test_a_command_that_touches_nothing_under_the_root_has_no_radius(tmp_path: Path) -> None:
    """A run whose executed files all live outside the repository is not a
    radius of zero paths — it is no radius at all."""
    root = tmp_path / "empty"
    root.mkdir()
    radius = _coverage.extract(f"{sys.executable} -m this", root, timeout_s=BUDGET)
    assert radius is None


# --- M1 review fixes: interpreter hardening and config neutrality -------------


def test_a_repo_relative_interpreter_is_never_executed(tmp_path: Path) -> None:
    """`./python3` is exactly the binary an attacker can commit (T27;
    M1 review finding 5)."""
    sentinel = tmp_path / "executed"
    fake = tmp_path / "python3"
    fake.write_text(f"#!/bin/sh\ntouch {sentinel}\n")
    fake.chmod(0o755)
    radius = _coverage.extract("./python3 -m pytest tests/", tmp_path, timeout_s=30)
    assert radius is None
    assert not sentinel.exists(), "the repo-local interpreter must never run"


def test_an_absolute_interpreter_under_root_is_never_executed(tmp_path: Path) -> None:
    sentinel = tmp_path / "executed"
    fake = tmp_path / "python3"
    fake.write_text(f"#!/bin/sh\ntouch {sentinel}\n")
    fake.chmod(0o755)
    radius = _coverage.extract(f"{fake} -m pytest tests/", tmp_path, timeout_s=30)
    assert radius is None
    assert not sentinel.exists()


def test_a_bare_python_name_maps_to_sys_executable_not_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare `python3` must resolve to sys.executable, never to PATH — a
    PATH lookup is one attacker-controlled variable away from finding 5."""
    trap_dir = tmp_path / "bin"
    trap_dir.mkdir()
    sentinel = tmp_path / "executed"
    trap = trap_dir / "python3"
    trap.write_text(f"#!/bin/sh\ntouch {sentinel}\n")
    trap.chmod(0o755)
    monkeypatch.setenv("PATH", str(trap_dir))
    project = tmp_path / "proj"
    (project / "tests").mkdir(parents=True)
    (project / "mod.py").write_text("V = 1\n")
    (project / "conftest.py").write_text("")
    (project / "tests" / "test_mod.py").write_text(
        "import mod\n\n\ndef test_v():\n    assert mod.V == 1\n"
    )
    radius = _coverage.extract("python3 -m pytest -q tests/", project, timeout_s=120)
    assert not sentinel.exists(), "PATH must never choose the interpreter"
    assert radius is not None
    assert "tests/test_mod.py" in radius.paths


def test_the_projects_coverage_config_cannot_shrink_the_radius(tmp_path: Path) -> None:
    """[run] source/omit in the project's own config must not drop files that
    ran (M1 review finding 4) — a radius describes what executed, not what
    the project chose to report on."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "mod.py").write_text("V = 1\n")
    (tmp_path / "conftest.py").write_text("")
    (tmp_path / "tests" / "test_mod.py").write_text(
        "import mod\n\n\ndef test_v():\n    assert mod.V == 1\n"
    )
    (tmp_path / ".coveragerc").write_text("[run]\nsource = mod\nomit = tests/*\n")
    radius = _coverage.extract(f"{sys.executable} -m pytest -q tests/", tmp_path, timeout_s=120)
    assert radius is not None
    assert "tests/test_mod.py" in radius.paths, "omit must not shrink the radius"
    assert "mod.py" in radius.paths
