"""The gated re-verification executor: re-run a record's own evidence command.

This is the dangerous component (T27): automatic re-execution of stored
commands is a code-execution path that exists nowhere else in Provalume, so
every control here is a refusal by default. The command comes from the
RECORD — ``content["raw_command"]``, the string that actually ran at
verification time (``content["command"]`` is the *normalized* form, which
collapses whitespace even inside quoted arguments; re-running it would let
the engine's own rewriting break a true claim) — never from the caller: the
executor re-runs the claim's own evidence command or nothing, exactly as
the original verification did, which is also where its authority to move
freshness comes from (I4: no agent gains write power; there is no MCP
surface to this).

Controls, in order, each refusing with a log and **no event** (a refusal is
policy working, not an execution to journal):

1. the record exists, belongs to this project AND this repository, and is
   not terminal — a database carried into a different tree must not execute
   that tree's files against another repository's records;
2. its trust is ``verified`` or above — agent-sourced records cap at
   ``observed`` and can never reach this code without independent evidence
   having promoted them first;
3. it is a **procedural record whose verification passed**. A gotcha's
   claim IS the failure — a failing re-run confirms it and a passing one
   obsoletes it, the exact inverse of this executor's outcome mapping —
   and an episodic record is a claim about the past that no re-run today
   can falsify. Both refuse (LIMITATIONS §9g);
4. a re-runnable command exists and is faithful: absent, empty, or
   containing the ``<TMP>`` normalization placeholder → refuse;
5. the allowlist is non-empty (empty = the feature is off — the default)
   and the command matches one of its patterns (``fnmatch.fnmatchcase``:
   case-sensitive on every platform; note ``*`` spans spaces, so anchor
   patterns at the start of the command);
6. the command parses into a non-empty argument vector, and the timeout is
   positive;
7. the working tree is clean of tracked modifications and its state can be
   certified. Worktree state never *triggers* freshness (ADR-0020); it
   must not get to *clear* it either — an uncommitted local fix answering
   for a landed breaking commit would be a false ``current``.

Execution is ``shlex``-split argv — never ``shell=True`` — under a hard
timeout, with ``cwd`` pinned to the given root. Outcome ``passed``/``failed``
comes from the exit code; a **negative** return code is a signal death (OOM
killer, SIGTERM) and journals ``errored`` — the process's demise is the
environment's doing, not evidence against the claim (I5). The event names
``executed_at_commit`` (the tree's HEAD) and ``answers_triggers`` — exactly
the outstanding triggers that are ancestors of that HEAD — so a passing run
at a detached or older HEAD discharges only what it actually answered.

``environment_fingerprint`` is computed **before** the run (a command that
edits the lockfile must not fingerprint its own aftermath) over the
interpreter version and the dependency lockfile — without it, ``stale``
cannot distinguish "the code broke this" from "the environment drifted".

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
from typing import TYPE_CHECKING, Any, Final

from provalume.schemas.events import EventType
from provalume.schemas.freshness import ReverificationOutcome
from provalume.schemas.memories import MemoryType
from provalume.schemas.trust import (
    Source,
    TrustState,
    VerificationState,
    is_terminal,
    meets,
)
from provalume.store.gitinfo import GitInfo

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


def _refused(
    pv: Provalume, record_id: str, allowlist: tuple[str, ...], timeout_s: float
) -> str | None:
    """The T27 control gauntlet. Returns the executable command string when
    every control passes, ``None`` (with the refusal logged) otherwise."""
    memory = pv.memories.get(record_id)
    if memory is None:
        log.warning("reverify refused: record %s does not exist", record_id)
        return None
    if memory.scope.project_id != pv.project_id:
        log.warning("reverify refused: record %s belongs to another project", record_id)
        return None
    record_repo = memory.scope.repository_id
    if record_repo and pv.repository_id and record_repo != pv.repository_id:
        log.warning(
            "reverify refused: record %s belongs to repository %r, this tree is %r — "
            "a foreign tree must not answer for it (T27)",
            record_id,
            record_repo,
            pv.repository_id,
        )
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
    if (
        memory.memory_type is not MemoryType.PROCEDURAL
        or memory.verification_state is not VerificationState.PASSED
    ):
        log.warning(
            "reverify refused: record %s is %s/%s — only a procedural record whose "
            "verification passed has re-run semantics this executor can interpret "
            "(a gotcha's failure IS its claim; see LIMITATIONS §9g)",
            record_id,
            memory.memory_type.value,
            memory.verification_state.value,
        )
        return None
    command = str(memory.content.get("raw_command") or memory.content.get("command") or "")
    if not command:
        log.warning("reverify refused: record %s has no command to re-run", record_id)
        return None
    if "<TMP>" in command:
        log.warning(
            "reverify refused: command of record %s contains the <TMP> normalization "
            "placeholder and cannot be faithfully re-run",
            record_id,
        )
        return None
    if not allowlist:
        log.warning("reverify refused: the allowlist is empty — re-execution is off (T27)")
        return None
    if not any(fnmatch.fnmatchcase(command, pattern) for pattern in allowlist):
        log.warning(
            "reverify refused: command of record %s matches no allowlist pattern",
            record_id,
        )
        return None
    if timeout_s <= 0:
        log.warning("reverify refused: non-positive timeout %r", timeout_s)
        return None
    return command


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
    a control refused, the engine failed before execution, or — logged
    loudly, because T27 requires every execution journaled — journaling
    failed after the command already ran. Never lets an ``Exception``
    escape (I5); ``BaseException`` such as ``KeyboardInterrupt`` propagates.
    """
    try:
        command = _refused(pv, record_id, allowlist, timeout_s)
        if command is None:
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

        # Worktree gate: a re-run answers for LANDED commits, so the tree
        # must actually be at a landed state. Git-less roots have no landed
        # commits and no triggers; the gate is vacuous there.
        git = GitInfo(cwd)
        executed_at = ""
        answers: list[str] = []
        if git.available:
            dirty = git.worktree_dirty()
            if dirty is None:
                log.warning("reverify refused: cannot certify the worktree state of %s", cwd)
                return None
            if dirty:
                log.warning(
                    "reverify refused: %s has uncommitted tracked changes — worktree "
                    "state must not answer for landed commits (commit or stash first)",
                    cwd,
                )
                return None
            executed_at = git.current_commit() or ""
            if executed_at:
                outstanding = pv.memories.outstanding_triggers(
                    project_id=pv.project_id, record_id=record_id
                )
                answers = [t for t in outstanding if git.is_ancestor(t, executed_at) is True]

        # Before the run: a command that rewrites the lockfile must not
        # fingerprint its own aftermath.
        fingerprint = environment_fingerprint(cwd)

        started = time.monotonic()
        exit_code: int | None = None
        signal: int | None = None
        try:
            completed = subprocess.run(  # noqa: S603 - argv from the record, gated above, never a shell  # nosec B603
                argv,
                cwd=cwd,
                capture_output=True,
                timeout=timeout_s,
                check=False,
            )
            exit_code = completed.returncode
            if exit_code is not None and exit_code < 0:
                # A signal death (OOM killer, SIGTERM) is the environment
                # acting on the process, not the command answering the
                # claim (I5): never evidence against the record.
                signal = -exit_code
                outcome = ReverificationOutcome.ERRORED
            elif exit_code == 0:
                outcome = ReverificationOutcome.PASSED
            else:
                outcome = ReverificationOutcome.FAILED
        except subprocess.TimeoutExpired:
            outcome = ReverificationOutcome.ERRORED
        except Exception:
            log.warning("reverify engine error; outcome=errored", exc_info=True)
            outcome = ReverificationOutcome.ERRORED
        duration_ms = int((time.monotonic() - started) * 1000)

        payload: dict[str, Any] = {
            "record_id": record_id,
            "trigger_commit": trigger_commit,
            "command": command,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "timeout_ms": int(timeout_s * 1000),
            "environment_fingerprint": fingerprint,
            "outcome": outcome.value,
            "executed_at_commit": executed_at,
            "answers_triggers": answers,
            "root": str(cwd),
        }
        if signal is not None:
            payload["signal"] = signal
        try:
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
            log.error(
                "reverify: the command EXECUTED (outcome %s, exit %r) but journaling "
                "failed — T27 requires every execution journaled; this one is only in "
                "this log line",
                outcome.value,
                exit_code,
                exc_info=True,
            )
            return None
    except Exception:
        log.warning("reverify failed open; record left in its prior state", exc_info=True)
        return None
