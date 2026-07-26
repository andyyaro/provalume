"""``coverage`` extraction: run the command under coverage.py and record what
actually executed.

The most precise method and the only one that executes anything — which is
why it never runs implicitly. The orchestrator invokes it only when an
operator passed ``method=BlastRadiusMethod.COVERAGE`` explicitly, with a
timeout, in the same operator-present posture as the original verification.

coverage.py is a dev/optional dependency invoked **as a subprocess**, never
imported into the core (the I1 purity guard enforces this: only stdlib and
provalume may be imported here).

Contract (frozen at M0/M1 skeleton):

``extract(command, root, *, timeout_s) -> BlastRadius | None``

- Parse ``command`` (``shlex``). Only Python invocations are supported:
  ``python -m mod …`` / ``python script.py …`` / a bare ``pytest …``-style
  console entry that can be rewritten to ``python -m``. Anything else
  returns ``None`` — this method does not guess.
- Re-invoke as ``[interpreter, "-m", "coverage", "run", "--data-file",
  <tmp>, …original entry…]`` with ``cwd=root``, argument vectors only,
  ``timeout=timeout_s``. A missing coverage module, a timeout, or a non-zero
  coverage tooling failure returns ``None`` (the *command's own* exit code
  is not a failure of extraction — a failing test run still has a radius).
- Read the results via ``[interpreter, "-m", "coverage", "json", "-o", "-"]``
  (or an equivalent data-file read), also as a subprocess; collect executed
  files under ``root`` and compress executed line numbers into inclusive
  ``(start, end)`` ranges.
- Return ``BlastRadius(method=COVERAGE, paths=<sorted POSIX paths relative
  to root>, line_ranges={path: ranges}, tool="coverage.py",
  tool_version=<from `coverage --version`>)``.
- Clean up the temporary data file; return ``None`` on any error
  (fail-open, I5). Never raise. Never use ``shell=True``.

Where the contract left a choice, M1 chose:

- **Interpreter selection.** A command that names a Python binary explicitly
  (``/path/to/python3 -m pytest``) is re-run under *that* binary: the radius
  must describe the environment the verification actually used, and the
  interpreter is most of that environment. A bare console entry (``pytest
  -q``) names no interpreter, so ``sys.executable`` stands in — the only
  interpreter this process can name truthfully.
- **Tooling probe first.** ``coverage --version`` runs before anything else.
  It is the cheap way to learn whether the tooling exists at all, and running
  it first means an environment without coverage.py never executes the
  operator's command.
- **Command failure is not extraction failure.** The ``coverage run`` exit
  code is deliberately never inspected: a failing test suite still executed
  code, and that is exactly what a radius describes. Coverage *tooling*
  failure surfaces differently — as a missing or unreadable data file, which
  the reporting step reports as an error and which does return ``None``.
- **Execution discipline (T27).** Argument vectors only, never ``shell=True``;
  every call carries a timeout drawn from one shared deadline, so the whole
  extraction — probe, run, report — is bounded by ``timeout_s``; the data
  file lives in a private temporary directory (passed both as ``--data-file``
  and as ``COVERAGE_FILE``) so the project's own ``.coverage`` is never
  touched, and that directory is removed on every path.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess  # nosec B404 - argv vectors only, never shell=True (T27)
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from provalume.freshness.blast_radius import MAX_RADIUS_PATHS, BlastRadius
from provalume.schemas.freshness import BlastRadiusMethod

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

log = logging.getLogger("provalume.freshness")

#: Recorded on every radius this module produces.
TOOL: Final = "coverage.py"

#: Basenames that count as "this argument names a Python interpreter":
#: ``python``, ``python3``, ``python3.13``, ``pythonw``, ``pypy3``.
_PYTHON_BINARY: Final = re.compile(r"^(?:python|pythonw|pypy)[0-9.]*$")

#: ``Coverage.py, version 7.15.2 with C extension`` -> ``7.15.2``.
_VERSION: Final = re.compile(r"\bversion\s+([^\s,]+)")

#: Console entry points that are also ``python -m``-runnable modules, so a
#: bare ``pytest tests/`` can be rewritten without guessing. Deliberately
#: short: an unlisted console script returns ``None`` rather than being
#: rewritten into an import that might mean something else entirely.
_CONSOLE_MODULES: Final = frozenset(
    {"pytest", "unittest", "tox", "nox", "mypy", "pylint", "pyflakes"}
)


def extract(command: str, root: Path, *, timeout_s: float) -> BlastRadius | None:
    """Run ``command`` under coverage.py and return what it executed.

    Fails open (I5): every failure — an unsupported command, missing
    coverage.py, a timeout, unreadable results, a radius over
    ``MAX_RADIUS_PATHS`` — returns ``None`` and raises nothing.
    """
    try:
        return _extract(command, root, timeout_s=timeout_s)
    except Exception:  # pragma: no cover - defence in depth; I5 is absolute
        log.warning("coverage blast-radius extraction failed open", exc_info=True)
        return None


def _extract(command: str, root: Path, *, timeout_s: float) -> BlastRadius | None:
    deadline = _Deadline(timeout_s)
    if deadline.remaining() <= 0:
        log.debug("coverage extraction: non-positive timeout")
        return None

    entry = _entry_point(command, root)
    if entry is None:
        log.debug("coverage extraction: %r is not a supported Python invocation", command)
        return None
    interpreter, program = entry

    # Probe the tooling before executing anything: an interpreter without
    # coverage.py must not run the operator's command at all.
    version = _tool_version(interpreter, root, deadline)
    if version is None:
        return None

    workspace = Path(tempfile.mkdtemp(prefix="provalume-coverage-"))
    try:
        data_file = workspace / "radius.coverage"
        environ = dict(os.environ)
        # Belt and braces: the flag governs this run, the variable governs any
        # subprocess the command spawns. Neither is the project's own file.
        environ["COVERAGE_FILE"] = str(data_file)

        run = _run(
            [interpreter, "-m", "coverage", "run", "--data-file", str(data_file), *program],
            root=root,
            deadline=deadline,
            env=environ,
        )
        if run is None:
            log.debug("coverage extraction: the run did not complete (timeout or no tooling)")
            return None
        # `run.returncode` is intentionally not inspected. A failing
        # verification command still executed code, and that is the radius.
        report = _run(
            [
                interpreter,
                "-m",
                "coverage",
                "json",
                "--data-file",
                str(data_file),
                "-o",
                "-",
                "-i",
                "--fail-under=0",
            ],
            root=root,
            deadline=deadline,
            env=environ,
        )
        if report is None or report.returncode != 0:
            # No data file, an unreadable one, or a coverage that rejects
            # these flags: tooling failure, which is extraction failure.
            log.debug("coverage extraction: could not read the coverage results")
            return None
        return _radius(report.stdout, root=root, version=version)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


# --- command parsing ----------------------------------------------------------


def _entry_point(command: str, root: Path) -> tuple[str, tuple[str, ...]] | None:
    """Split ``command`` into (interpreter, program spec), or ``None``.

    The program spec is what follows ``coverage run --data-file <tmp>``:
    either ``-m mod …`` or ``script.py …``, with the original arguments
    untouched (coverage stops parsing its own options at the program spec).
    """
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if not argv:
        return None

    head, rest = argv[0], argv[1:]
    if _is_python_binary(head):
        return _python_entry(head, rest, root)
    name = _basename(head)
    if name in _CONSOLE_MODULES:
        # A bare console entry names no interpreter, so this one stands in.
        return sys.executable, ("-m", name, *rest)
    return None


def _python_entry(
    interpreter: str, rest: Sequence[str], root: Path
) -> tuple[str, tuple[str, ...]] | None:
    if not rest:
        return None
    first = rest[0]
    if first == "-m":
        module = rest[1] if len(rest) > 1 else ""
        if not module or module.startswith("-"):
            return None
        return interpreter, tuple(rest)
    if first.startswith("-"):
        # `-c`, `-u`, `-X dev`, … are none of the three supported forms, and
        # guessing where the program spec starts is exactly the guessing this
        # method refuses to do.
        return None
    script = Path(first)
    if not script.is_absolute():
        script = root / script
    resolved = _resolved(script)
    if resolved is None or not _under(resolved, root) or not resolved.is_file():
        # A script outside the repository has no radius inside it, and
        # executing it would be a T27 boundary this method has no business
        # crossing.
        return None
    return interpreter, tuple(rest)


def _is_python_binary(argument: str) -> bool:
    return bool(_PYTHON_BINARY.match(_basename(argument)))


def _basename(argument: str) -> str:
    name = Path(argument).name
    return name[: -len(".exe")] if name.endswith(".exe") else name


# --- subprocess discipline ----------------------------------------------------


class _Deadline:
    """One wall-clock budget shared by every subprocess in the extraction."""

    def __init__(self, timeout_s: float) -> None:
        self._end = time.monotonic() + max(timeout_s, 0.0)

    def remaining(self) -> float:
        return self._end - time.monotonic()


def _run(
    argv: Sequence[str],
    *,
    root: Path,
    deadline: _Deadline,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """One argv-only, timed subprocess. ``None`` means it did not complete."""
    remaining = deadline.remaining()
    if remaining <= 0:
        return None
    try:
        return subprocess.run(  # noqa: S603 - argv vector, no shell  # nosec B603
            list(argv),
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=remaining,
            check=False,
            shell=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        # No such interpreter, a timeout, an undecodable stream: all of them
        # are "no result", none of them raise onward.
        return None


def _tool_version(interpreter: str, root: Path, deadline: _Deadline) -> str | None:
    result = _run([interpreter, "-m", "coverage", "--version"], root=root, deadline=deadline)
    if result is None or result.returncode != 0:
        log.debug("coverage extraction: coverage.py is not available to %s", interpreter)
        return None
    match = _VERSION.search(result.stdout)
    if match is None:
        log.debug("coverage extraction: unparseable version banner")
        return None
    return match.group(1)


# --- result shaping -----------------------------------------------------------


def _radius(report: str, *, root: Path, version: str) -> BlastRadius | None:
    try:
        document: Any = json.loads(report)
    except ValueError:
        log.debug("coverage extraction: the JSON report did not parse")
        return None
    if not isinstance(document, dict):
        return None
    files = document.get("files")
    if not isinstance(files, dict):
        return None

    root_resolved = _resolved(root)
    if root_resolved is None:
        return None

    executed: dict[str, set[int]] = {}
    for name, entry in files.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        reported = entry.get("executed_lines")
        if not isinstance(reported, list):
            continue
        lines: set[int] = {n for n in reported if isinstance(n, int) and n > 0}
        if not lines:
            # Imported but nothing ran (an empty conftest, a file the report
            # lists at 0%): not part of what this verification exercised.
            continue
        relative = _relative(name, root=root, root_resolved=root_resolved)
        if relative is None:
            # site-packages, the stdlib, anything outside the repository.
            continue
        executed.setdefault(relative, set()).update(lines)
        if len(executed) > MAX_RADIUS_PATHS:
            log.debug("coverage extraction: radius exceeds MAX_RADIUS_PATHS")
            return None

    if not executed:
        log.debug("coverage extraction: nothing under the root executed")
        return None
    paths = tuple(sorted(executed))
    return BlastRadius(
        method=BlastRadiusMethod.COVERAGE,
        paths=paths,
        line_ranges={path: _ranges(executed[path]) for path in paths},
        tool=TOOL,
        tool_version=version,
    )


def _relative(name: str, *, root: Path, root_resolved: Path) -> str | None:
    """A reported file name as a POSIX path under ``root``, or ``None``.

    coverage reports paths relative to its working directory (which is
    ``root``) where it can, absolute ones otherwise.
    """
    candidate = Path(name)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = _resolved(candidate)
    if resolved is None or not resolved.is_relative_to(root_resolved):
        return None
    return resolved.relative_to(root_resolved).as_posix()


def _resolved(path: Path) -> Path | None:
    try:
        return path.resolve()
    except OSError:
        return None


def _under(path: Path, root: Path) -> bool:
    resolved_root = _resolved(root)
    return resolved_root is not None and path.is_relative_to(resolved_root)


def _ranges(lines: set[int]) -> tuple[tuple[int, int], ...]:
    """Executed line numbers as sorted, inclusive, non-overlapping ranges."""
    ordered = sorted(lines)
    ranges: list[tuple[int, int]] = []
    start = previous = ordered[0]
    for line in ordered[1:]:
        if line == previous + 1:
            previous = line
            continue
        ranges.append((start, previous))
        start = previous = line
    ranges.append((start, previous))
    return tuple(ranges)
