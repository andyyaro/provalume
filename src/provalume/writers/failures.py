"""Failure signatures and gotcha memory.

A **failure signature** is what lets Provalume say "this has failed before"
without an LLM. It is a SHA-256 over a normalised
``(command, error_kind, error_fingerprint)`` tuple, where normalisation strips
everything that varies between two runs of the same failure: absolute paths, line
and column numbers, hex digests, timestamps, PIDs, durations, ports, temp
directories, memory addresses, and durations.

Normalisation is **deliberately lossy**, and the trade-off is real in both
directions:

* too aggressive and genuinely different failures collide, producing a warning
  about something that never happened;
* too timid and the same failure never matches itself, so the gate is silent
  exactly when it should speak.

Every rule below is individually tested, and the false-positive rate is an eval
metric (scenario 19) rather than an assumption. Two failures with the same
signature are *probably* the same failure — never certainly.
"""

from __future__ import annotations

import re
from typing import Any, Final, NamedTuple

from provalume import _time
from provalume.interchange.hashing import hash_text
from provalume.policy.invalidation import subject_key
from provalume.schemas.events import Event
from provalume.schemas.memories import Memory, MemoryType
from provalume.schemas.scope import Scope, ScopeLevel
from provalume.schemas.trust import TrustState, VerificationState

#: Normalisation rules, applied in order. Each replaces a varying token with a
#: stable placeholder so that the *shape* of the error survives and the
#: incidental detail does not.
_NORMALIZERS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    # Timestamps first: they contain digits and colons that later rules would
    # otherwise shred into meaningless fragments.
    (
        re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"),
        "<TS>",
    ),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b"), "<TIME>"),
    # Temp directories, which differ on every run.
    (re.compile(r"/(?:private/)?(?:tmp|var/folders)/[^\s:,)\"']*"), "<TMP>"),
    (re.compile(r"(?i)\bC:\\\\(?:Users|Windows)\\\\Temp\\\\[^\s:,)\"']*"), "<TMP>"),
    # Absolute POSIX and Windows paths -> keep only the basename, because the
    # file that failed is signal and where the checkout lives is not.
    (re.compile(r"(?:/[\w.\-+@]+){2,}/([\w.\-+@]+)"), r"<PATH>/\1"),
    (re.compile(r"(?i)\b[A-Z]:\\\\(?:[\w.\-+@]+\\\\)+([\w.\-+@]+)"), r"<PATH>/\1"),
    # Memory addresses and long hex digests.
    (re.compile(r"\b0x[0-9a-fA-F]{4,}\b"), "<ADDR>"),
    (re.compile(r"\b[0-9a-f]{32,64}\b"), "<HASH>"),
    (re.compile(r"\b[0-9a-f]{7,12}\b(?=\s|$|[).,])"), "<SHORTHASH>"),
    # UUIDs and ULIDs.
    (
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        "<UUID>",
    ),
    (re.compile(r"\b[0-7][0-9A-HJKMNP-TV-Z]{25}\b"), "<ULID>"),
    # Positions and identifiers.
    (re.compile(r"(?i)\bline\s+\d+"), "line <N>"),
    (re.compile(r":\d+:\d+\b"), ":<N>:<N>"),
    (re.compile(r"(?i)\b(?:pid|process)\s*[:=]?\s*\d+"), "pid <N>"),
    (re.compile(r"(?i)\bport\s*[:=]?\s*\d{2,5}"), "port <N>"),
    (re.compile(r"\b(?:127\.0\.0\.1|localhost|0\.0\.0\.0):\d{2,5}\b"), "<HOSTPORT>"),
    # Durations and counts.
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|s|sec|secs|seconds|m|min|mins|h|hrs)\b"), "<DUR>"),
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:B|KB|MB|GB|KiB|MiB|GiB)\b"), "<SIZE>"),
    # Any remaining bare number of 2+ digits. Single digits survive because
    # "exit 1" and "exit 2" are genuinely different failures.
    (re.compile(r"\b\d{2,}\b"), "<N>"),
    # Collapse whitespace last.
    (re.compile(r"\s+"), " "),
)

#: Command normalisation is separate and much more conservative. Flags and
#: arguments are meaningful — `pytest -n auto` and `pytest -p no:xdist` are
#: different procedures — so only whitespace and temp paths are normalised.
_COMMAND_NORMALIZERS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"/(?:private/)?(?:tmp|var/folders)/[^\s]*"), "<TMP>"),
    (re.compile(r"\s+"), " "),
)

#: How much of the error text contributes. Bounded so a megabyte of traceback
#: does not make the signature depend on the tail of a log.
_FINGERPRINT_CHARS: Final = 400

#: Lines that carry no failure identity, skipped when picking the fingerprint.
_NOISE_LINE = re.compile(
    r"^\s*(?:\.{3}|=+|-+|\*+|#+|\[\s*\]|Traceback \(most recent call last\):?)\s*$"
)


class Signature(NamedTuple):
    """A computed failure signature and the parts it came from.

    ``normalized`` is kept so ``explain`` can show *why* two failures matched.
    An opaque hash would make a false positive impossible to diagnose.
    """

    value: str
    command: str
    error_kind: str
    normalized: str

    def short(self) -> str:
        return self.value.removeprefix("sha256:")[:12]


def normalize_error(text: str) -> str:
    """Strip run-varying detail from error text."""
    out = text.strip()
    for pattern, replacement in _NORMALIZERS:
        out = pattern.sub(replacement, out)
    return out.strip().lower()


def normalize_command(command: str) -> str:
    """Normalise a command, preserving flags and arguments."""
    out = command.strip()
    for pattern, replacement in _COMMAND_NORMALIZERS:
        out = pattern.sub(replacement, out)
    return out.strip()


#: Strong signal: a line that *declares* an error. Three shapes, because
#: ecosystems disagree about what an error line looks like:
#:   - a name containing error/exception/panic/fault, colon-terminated —
#:     `ValueError: bad input`, `E   TimeoutError: deadlock`,
#:     `error[E0308]: mismatched types`
#:   - a fatal-class keyword — `FATAL: ...`, `Segmentation fault`
#:   - a bare CamelCase identifier, colon-terminated — `PoolExhausted: ...`,
#:     which is how custom exceptions are named in most languages and which the
#:     first shape misses entirely
#:
#: The CamelCase alternative uses ``[a-z0-9]*`` rather than ``[a-zA-Z0-9]*``
#: inside the repeated group. That is a **ReDoS fix, not a style choice**: with
#: uppercase allowed in the tail, ``AB`` could match as one iteration or two, and
#: a long run of mixed-case letters with no closing colon backtracked
#: exponentially — roughly 4x per two added characters, measured. Since this
#: pattern parses error text that ultimately comes from whatever a failing
#: command printed, that was a denial-of-service reachable from hostile input
#: (threat T24). Anchoring each iteration to exactly one uppercase letter makes
#: the split unambiguous and the match linear.
_STRONG_ERROR = re.compile(
    r"^(?:E\s+)?(?:"
    r"(?i:\[?[a-z_.]*(?:error|exception|panic|fault)\w*\]?)\s*(?:\[[^\]]+\])?\s*:"
    r"|(?i:fatal|panic|abort|segmentation fault)\b"
    r"|[A-Z][a-z0-9]*(?:[A-Z][a-z0-9]*)+\s*:"
    r")"
)

#: Weaker signal: a line that merely mentions failure. Used only if no line
#: declares an error, because an `assert foo()` source line is less identifying
#: than the exception it raised.
_WEAK_ERROR = re.compile(
    r"(?i)\b(?:error|exception|failed|failure|assert|fatal|panic|"
    r"cannot|unable|refused|denied|timeout|not found|no such)\b"
)


def _fingerprint(error_text: str) -> str:
    """Pick the most identifying line of an error.

    Two tiers, because the obvious single-pass version picks the wrong line. A
    Python traceback contains both the failing source line (``assert
    pool.acquire()``) and the exception that resulted (``E TimeoutError:
    deadlock in db fixture teardown``). The second is far more identifying — the
    same source line can raise different exceptions — so a line that *declares*
    an error wins over one that merely mentions failure.

    Both tiers skip framing like ``Traceback (most recent call last):``, which is
    identical across unrelated failures and would collapse them onto one
    signature.
    """
    lines = [ln.strip() for ln in error_text.splitlines() if ln.strip()]
    lines = [ln for ln in lines if not _NOISE_LINE.match(ln)]
    if not lines:
        return ""

    for line in lines:
        if _STRONG_ERROR.match(line):
            return line[:_FINGERPRINT_CHARS]
    for line in lines:
        if _WEAK_ERROR.search(line):
            return line[:_FINGERPRINT_CHARS]
    return lines[0][:_FINGERPRINT_CHARS]


def compute(
    *,
    command: str,
    error_kind: str = "",
    error_text: str = "",
) -> Signature:
    """Compute a failure signature.

    Deterministic: the same inputs always produce the same signature, on any
    machine and in any process. Required for the eval harness and for
    ``rebuild`` to reproduce projections byte-for-byte.
    """
    norm_command = normalize_command(command)
    norm_kind = error_kind.strip().lower()
    norm_error = normalize_error(_fingerprint(error_text))
    material = f"cmd={norm_command}\nkind={norm_kind}\nerr={norm_error}"
    return Signature(
        value=hash_text(material),
        command=norm_command,
        error_kind=norm_kind,
        normalized=material,
    )


def signature_from_event(event: Event) -> Signature:
    """Compute the signature for a failure event."""
    payload = event.payload
    return compute(
        command=str(payload.get("command", "")),
        error_kind=str(payload.get("error_kind", "")),
        error_text=str(payload.get("excerpt", payload.get("error", ""))),
    )


def _sentence(text: str) -> str:
    """Terminate a fragment so later clauses append as separate sentences.

    Gotcha text is assembled in stages — the failure, then the repeat count, then
    the resolution — each appended by a different writer at a different time.
    Without this, an error line that ends mid-phrase runs into the next clause
    ("...fixture teardown This has now failed twice").
    """
    stripped = text.strip()
    if not stripped or stripped[-1] in ".!?:;":
        return stripped
    return stripped + "."


def gotcha_text(*, command: str, error_kind: str, excerpt: str, branch: str | None) -> str:
    """Render the human-readable text for a gotcha.

    Deterministic string construction, no formatting that depends on locale or
    dict ordering — this text is hashed into ``content_hash``.
    """
    parts = [f"`{command.strip()}` failed"]
    if error_kind:
        parts.append(f"({error_kind})")
    if branch:
        parts.append(f"on branch {branch}")
    head = " ".join(parts) + "."
    first = _sentence(_fingerprint(excerpt))
    return f"{head} {first}" if first else head


def build_gotcha(
    event: Event,
    *,
    landing_state: TrustState,
    poisoning_risk: float = 0.0,
    poisoning_matches: tuple[str, ...] = (),
) -> tuple[Memory, Signature]:
    """Project a verification-failure event into a gotcha candidate.

    A pure function of the event: the same event always produces the same record,
    including its ``memory_id``, which is derived from the event rather than
    generated. That is what lets ``rebuild`` reconstruct projections identically
    instead of minting new identifiers on every run.
    """
    signature = signature_from_event(event)
    payload = event.payload
    command = str(payload.get("command", ""))
    error_kind = str(payload.get("error_kind", ""))
    excerpt = str(payload.get("excerpt", payload.get("error", "")))

    text = gotcha_text(
        command=command, error_kind=error_kind, excerpt=excerpt, branch=event.branch
    )
    content: dict[str, Any] = {
        "command": command,
        "error_kind": error_kind,
        "excerpt": excerpt[:2000],
        "failure_signature": signature.value,
        "exit_code": payload.get("exit_code"),
        "resolution": None,
    }

    memory = Memory(
        memory_id=_derive_memory_id(event.event_id, "gotcha"),
        memory_type=MemoryType.GOTCHA,
        content=content,
        text=text,
        scope=Scope(
            level=ScopeLevel.BRANCH if event.branch else ScopeLevel.REPOSITORY,
            project_id=event.project_id,
            repository_id=event.repository_id,
            branch=event.branch,
            run_id=event.run_id,
            task_id=event.task_id,
            attempt_id=event.attempt_id,
            agent_profile=event.agent_profile,
        ),
        run_id=event.run_id,
        task_id=event.task_id,
        attempt_id=event.attempt_id,
        source_event_ids=(event.event_id,),
        source=event.source,
        author_agent=event.agent_profile,
        adapter=event.adapter,
        model=event.model,
        effort=event.effort,
        commit_sha=event.commit_sha,
        valid_at=event.recorded_at,
        recorded_at=event.recorded_at,
        trust_state=landing_state,
        # The failure IS the evidence. trust_state=verified alongside
        # verification_state=failed is coherent and is the case that forced these
        # to be separate fields (ADR-0005).
        verification_state=VerificationState.FAILED,
        poisoning_risk=poisoning_risk,
        poisoning_matches=poisoning_matches,
        subject_key=subject_key(f"{command} {error_kind}"),
    )
    return _with_content_hash(memory), signature


def build_resolution_link(
    *,
    gotcha: Memory,
    resolution_event: Event,
) -> Memory:
    """Attach what finally worked to a gotcha.

    This is the link that turns "this broke" into "this broke, and here is the
    fix", which is the difference between a warning that helps and one that just
    says no.
    """
    content = dict(gotcha.content)
    content["resolution"] = {
        "command": resolution_event.payload.get("command", ""),
        "event_id": resolution_event.event_id,
        "recorded_at": resolution_event.recorded_at,
        "commit_sha": resolution_event.commit_sha,
    }
    resolved_command = str(content["resolution"]["command"]).strip()
    text = gotcha.text
    if resolved_command and "What later worked" not in text:
        text = f"{_sentence(text)} What later worked: `{resolved_command}`."

    updated = gotcha.model_copy(
        update={
            "content": content,
            "text": text[: 8192],
            "source_event_ids": tuple(
                dict.fromkeys([*gotcha.source_event_ids, resolution_event.event_id])
            ),
        }
    )
    return _with_content_hash(updated)


def _derive_memory_id(event_id: str, kind: str) -> str:
    """Derive a stable memory identifier from its originating event.

    Deterministic so a rebuild produces the same identifiers rather than new
    ones. Prefixed with the kind so one event can project into several records
    (a failure produces both a gotcha and an episodic entry) without collision.
    """
    return hash_text(f"{kind}:{event_id}").removeprefix("sha256:")[:26].upper()


def _with_content_hash(memory: Memory) -> Memory:
    from provalume.interchange.hashing import hash_content

    return memory.model_copy(
        update={"content_hash": hash_content(memory.content, memory.text)}
    )


def merge_occurrence(gotcha: Memory, event: Event, occurrences: int) -> Memory:
    """Fold a repeat occurrence into an existing gotcha.

    Repetition is what elevates a note into a warning, so the count is recorded
    on the record rather than only in the signature table — a digest showing the
    gotcha should be able to say "twice" without a second query.
    """
    content = dict(gotcha.content)
    content["occurrences"] = occurrences
    content["last_seen_at"] = event.recorded_at
    updated = gotcha.model_copy(
        update={
            "content": content,
            "source_event_ids": tuple(
                dict.fromkeys([*gotcha.source_event_ids, event.event_id])
            ),
            "valid_at": min(gotcha.valid_at, event.recorded_at),
        }
    )
    return _with_content_hash(updated)


def elevated_warning_text(gotcha: Memory, occurrences: int) -> str:
    """Wording for a repeated failure, used by the preflight gate."""
    if occurrences <= 1:
        return gotcha.text
    times = "twice" if occurrences == 2 else f"{occurrences} times"
    return f"{_sentence(gotcha.text)} This has now failed {times}."


def age_days(event: Event, *, reference: str | None = None) -> float:
    return _time.age_days(event.recorded_at, reference=reference)
