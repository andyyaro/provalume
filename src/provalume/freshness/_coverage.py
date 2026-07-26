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
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

    from provalume.freshness.blast_radius import BlastRadius


def extract(command: str, root: Path, *, timeout_s: float) -> BlastRadius | None:
    """Not yet implemented (M1 fan-out unit); fail open until it is."""
    return None
