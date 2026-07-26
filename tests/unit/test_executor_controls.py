"""The T27 control set on the re-verification executor, one refusal at a time.

Every control refuses with a log and NO event: a refusal is policy working,
not an execution to journal. Each test pairs the refusal with the positive
control implied by the rest of the file — the same record executes fine when
the controls are satisfied (spec §5.4)."""

from __future__ import annotations

import subprocess  # nosec B404 - spy target only; nothing here executes for real
import sys
from pathlib import Path

import pytest

from provalume.freshness.executor import environment_fingerprint, reverify_record
from provalume.schemas.events import EventType
from provalume.schemas.memories import MemoryType
from provalume.sdk.client import Provalume

PASS = f"{sys.executable} -c 'raise SystemExit(0)'"
FAIL = f"{sys.executable} -c 'raise SystemExit(3)'"


def _verified_record(pv: Provalume, command: str = PASS) -> str:
    pv.record_verification(command=command, passed=True)
    return next(
        m.memory_id
        for m in pv.memory_records(limit=20)
        if m.memory_type is MemoryType.PROCEDURAL and m.content.get("command") == command
    )


def _executions(pv: Provalume) -> list:
    return [e for e in pv.events() if e.event_type == EventType.REVERIFICATION_EXECUTED]


def test_a_missing_record_refuses_without_an_event(pv: Provalume) -> None:
    assert (
        reverify_record(
            pv, record_id="no-such-record", trigger_commit="", allowlist=("*",), timeout_s=5.0
        )
        is None
    )
    assert not _executions(pv)


def test_trust_below_verified_refuses(pv: Provalume) -> None:
    """An agent proposal is quarantined; nothing in its payload — including a
    command field — can reach the executor (T27's trust floor)."""
    pv.propose(text="run this", memory_type="procedural", content={"command": PASS}, agent="mal")
    proposed = [m for m in pv.memory_records(limit=20) if m.content.get("command") == PASS]
    assert proposed, "precondition: the proposal projected into a record"
    assert (
        reverify_record(
            pv,
            record_id=proposed[0].memory_id,
            trigger_commit="",
            allowlist=("*",),
            timeout_s=5.0,
        )
        is None
    )
    assert not _executions(pv)


def test_an_empty_allowlist_means_the_feature_is_off(pv: Provalume) -> None:
    record_id = _verified_record(pv)
    assert (
        reverify_record(pv, record_id=record_id, trigger_commit="", allowlist=(), timeout_s=5.0)
        is None
    )
    assert not _executions(pv)


def test_a_non_matching_pattern_refuses(pv: Provalume) -> None:
    record_id = _verified_record(pv)
    assert (
        reverify_record(
            pv,
            record_id=record_id,
            trigger_commit="",
            allowlist=("pytest *", "make test"),
            timeout_s=5.0,
        )
        is None
    )
    assert not _executions(pv)


def test_a_record_without_a_command_refuses(pv: Provalume) -> None:
    pv.record_fact(subject="the sky", statement="it is blue")
    factual = [m for m in pv.memory_records(limit=20) if not m.content.get("command")]
    assert factual, "precondition: a record with no command exists"
    assert (
        reverify_record(
            pv, record_id=factual[0].memory_id, trigger_commit="", allowlist=("*",), timeout_s=5.0
        )
        is None
    )
    assert not _executions(pv)


def test_the_command_comes_from_the_record_never_the_caller(pv: Provalume) -> None:
    """There is no argument through which a caller can supply a command —
    the surface admits a record id and policy only. What executes is
    exactly the recorded evidence command."""
    record_id = _verified_record(pv, command=FAIL)
    event = reverify_record(
        pv, record_id=record_id, trigger_commit="", allowlist=("*",), timeout_s=30.0
    )
    assert event is not None
    assert event.payload["command"] == FAIL
    assert event.payload["outcome"] == "failed"
    assert event.payload["exit_code"] == 3


def test_never_a_shell_argv_only(pv: Provalume, monkeypatch: pytest.MonkeyPatch) -> None:
    """The spy sees the exact argv and proves shell metacharacters stay
    literal arguments: no `shell=True`, no string command, ever."""
    command = f'{sys.executable} -c "raise SystemExit(0)" "; rm -rf /" "$(reboot)"'
    record_id = _verified_record(pv, command=command)
    seen: dict[str, object] = {}
    original_run = subprocess.run

    def spy(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
        seen["argv"] = argv
        seen["shell"] = kwargs.get("shell", False)
        return original_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    event = reverify_record(
        pv, record_id=record_id, trigger_commit="", allowlist=("*",), timeout_s=30.0
    )
    assert event is not None and event.payload["outcome"] == "passed"
    assert isinstance(seen["argv"], list)
    assert seen["argv"] == [sys.executable, "-c", "raise SystemExit(0)", "; rm -rf /", "$(reboot)"]
    assert seen["shell"] is False


def test_a_timeout_records_errored_and_the_configured_bound(pv: Provalume) -> None:
    """A timeout kill is the engine's bound, not proof the claim is false:
    outcome `errored`, no freshness transition, and `timeout_ms` in the
    event so the kill is distinguishable from an ordinary failure."""
    command = f"{sys.executable} -c 'import time; time.sleep(60)'"
    record_id = _verified_record(pv, command=command)
    before = pv.memories.get(record_id)
    assert before is not None
    event = reverify_record(
        pv, record_id=record_id, trigger_commit="", allowlist=("*",), timeout_s=0.5
    )
    assert event is not None
    assert event.payload["outcome"] == "errored"
    assert event.payload["exit_code"] is None
    assert event.payload["timeout_ms"] == 500
    after = pv.memories.get(record_id)
    assert after is not None and after.freshness is before.freshness


def test_the_environment_fingerprint_names_interpreter_and_lockfile(tmp_path: Path) -> None:
    bare = environment_fingerprint(tmp_path)
    assert bare.startswith("sha256:")
    assert environment_fingerprint(tmp_path) == bare, "deterministic for a fixed root"
    (tmp_path / "requirements.txt").write_text("provalume==0.1\n")
    with_lock = environment_fingerprint(tmp_path)
    assert with_lock != bare, "the lockfile participates"
    (tmp_path / "uv.lock").write_text("[[package]]\n")
    assert environment_fingerprint(tmp_path) != with_lock, "uv.lock takes precedence"
