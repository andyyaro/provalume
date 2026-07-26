"""The gated re-verification executor: re-run a record's own evidence command.

This is the dangerous component (T27): automatic re-execution of stored
commands is a code-execution path that exists nowhere else in Provalume, so
every control here is a refusal by default. The command comes from the
RECORD — ``content["command"]`` — never from the caller: the executor
re-runs the claim's own evidence command or nothing, exactly as the original
verification did, which is also where its authority to move freshness comes
from (I4: no agent gains write power; there is no MCP surface to this).

Controls, in order, each refusing with a log and **no event** (a refusal is
policy working, not an execution to journal):

1. the record exists, belongs to this project, and is not terminal;
2. its trust is ``verified`` or above — agent-sourced records cap at
   ``observed`` and can never reach this code without independent evidence
   having promoted them first;
3. the allowlist is non-empty (empty = the feature is off — the default)
   and the record's command matches one of its ``fnmatch`` patterns;
4. the command parses into a non-empty argument vector.

Execution is ``shlex``-split argv — never ``shell=True`` — under a hard
timeout, with ``cwd`` pinned to the given root. Outcome ``passed``/``failed``
comes from the exit code; a timeout or an engine error journals
``errored``, because a bound exceeded is the engine's bound, not proof the
claim is false (I5: the engine's failure is never evidence against the
record, and projection makes no transition on ``errored``).

``environment_fingerprint`` is a hash over the interpreter version and the
dependency lockfile — without it, ``stale`` cannot distinguish "the code
broke this" from "the environment drifted".

There is no batch mode. One record per call keeps the operator in the loop
per execution (D15).
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import shlex
import subprocess  # nosec B404 - the executor's entire purpose, gated by T27 controls
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Final

from provalume.schemas.events import EventType
from provalume.schemas.freshness import ReverificationOutcome
from provalume.schemas.trust import Source, TrustState, is_terminal, meets

if TYPE_CHECKING:  # pragma: no cover - typing only
    from provalume.schemas.events import Event
    from provalume.sdk.client import Provalume

log = logging.getLogger("provalume.freshness")

#: Lockfiles consulted for the environment fingerprint, most specific first.
_LOCKFILES: Final = ("uv.lock", "poetry.lock", "requirements.txt")


def environment_fingerprint(root: Path) -> str:
    """``sha256:`` over the interpreter version and the first present
    lockfile's bytes at ``root`` (empty bytes when none exists)."""
    digest = hashlib.sha256()
    digest.update(sys.version.encode())
    for name in _LOCKFILES:
        candidate = root / name
        try:
            if candidate.is_file():
                digest.update(candidate.read_bytes())
                break
        except OSError:
            # An unreadable lockfile fingerprints as absent; the point is
            # comparability across runs, not completeness.
            continue
    return f"sha256:{digest.hexdigest()}"


def reverify_record(
    pv: Provalume,
    *,
    record_id: str,
    trigger_commit: str,
    allowlist: tuple[str, ...],
    timeout_s: float,
    root: Path | None = None,
) -> Event | None:
    """Re-run the record's own verification command under the T27 controls.

    Returns the appended ``reverification.executed`` event, or ``None`` when
    a control refused or the engine itself failed before anything could be
    journaled. Never raises (I5)."""
    try:
        memory = pv.memories.get(record_id)
        if memory is None:
            log.warning("reverify refused: record %s does not exist", record_id)
            return None
        if memory.scope.project_id != pv.project_id:
            log.warning("reverify refused: record %s belongs to another project", record_id)
            return None
        if is_terminal(memory.trust_state):
            log.warning(
                "reverify refused: record %s is terminal (%s) — nothing left to re-check",
                record_id,
                memory.trust_state.value,
            )
            return None
        if not meets(memory.trust_state, TrustState.VERIFIED):
            log.warning(
                "reverify refused: record %s is %s, below the verified floor (T27)",
                record_id,
                memory.trust_state.value,
            )
            return None
        command = str(memory.content.get("command", ""))
        if not command:
            log.warning("reverify refused: record %s has no command to re-run", record_id)
            return None
        if not allowlist:
            log.warning("reverify refused: the allowlist is empty — re-execution is off (T27)")
            return None
        if not any(fnmatch.fnmatch(command, pattern) for pattern in allowlist):
            log.warning(
                "reverify refused: command of record %s matches no allowlist pattern",
                record_id,
            )
            return None
        try:
            argv = shlex.split(command)
        except ValueError:
            log.warning(
                "reverify refused: command of record %s does not parse as an argument vector",
                record_id,
            )
            return None
        if not argv:
            log.warning("reverify refused: record %s has an empty command", record_id)
            return None

        cwd = root
        if cwd is None and pv.git is not None and getattr(pv.git, "available", False):
            cwd = Path(pv.git.root)
        if cwd is None:
            cwd = Path.cwd()

        started = time.monotonic()
        exit_code: int | None = None
        try:
            completed = subprocess.run(  # noqa: S603 - argv from the record, gated above, never a shell  # nosec B603
                argv,
                cwd=cwd,
                capture_output=True,
                timeout=timeout_s,
                check=False,
            )
            exit_code = completed.returncode
            outcome = (
                ReverificationOutcome.PASSED if exit_code == 0 else ReverificationOutcome.FAILED
            )
        except subprocess.TimeoutExpired:
            outcome = ReverificationOutcome.ERRORED
        except Exception:
            log.warning("reverify engine error; outcome=errored", exc_info=True)
            outcome = ReverificationOutcome.ERRORED
        duration_ms = int((time.monotonic() - started) * 1000)

        payload = {
            "record_id": record_id,
            "trigger_commit": trigger_commit,
            "command": command,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "timeout_ms": int(timeout_s * 1000),
            "environment_fingerprint": environment_fingerprint(cwd),
            "outcome": outcome.value,
        }
        if trigger_commit:
            return pv.record_event(
                EventType.REVERIFICATION_EXECUTED,
                source=Source.KERNEL,
                payload=payload,
                commit_sha=trigger_commit,
            )
        return pv.record_event(
            EventType.REVERIFICATION_EXECUTED, source=Source.KERNEL, payload=payload
        )
    except Exception:
        log.warning("reverify failed open; record left in its prior state", exc_info=True)
        return None
