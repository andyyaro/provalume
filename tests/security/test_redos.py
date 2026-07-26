"""Every regex that touches attacker-influenced text must run in linear time.

Provalume compiles patterns against three kinds of hostile input: error excerpts
from whatever a failing command printed, memory text that may have come from a
compromised agent, and imported JSONL. A regex with ambiguous nested quantifiers
turns any of those into a denial of service (threat T24).

CodeQL caught one such pattern in ``_STRONG_ERROR``: with uppercase allowed
inside the repeated group, ``AB`` could match as one iteration or two, and a long
mixed-case run with no closing colon backtracked at roughly 4x per two added
characters. These tests exist so a future edit that reintroduces the shape fails
here rather than in production.
"""

from __future__ import annotations

import re
import time

import pytest

from provalume import redact
from provalume.freshness import _coverage
from provalume.policy import poisoning
from provalume.store import fts, gitinfo
from provalume.writers import failures

#: A pattern is linear enough if a 20k-character adversarial string matches in
#: well under a second. The exponential version took 1.5s at *24* characters, so
#: the gap between pass and fail is many orders of magnitude — there is no risk
#: of this being flaky on a slow runner.
BUDGET_S = 2.0
LENGTH = 20_000


def elapsed(fn: object, *args: object) -> float:
    start = time.perf_counter()
    fn(*args)  # type: ignore[operator]
    return time.perf_counter() - start


# --- The pattern CodeQL flagged --------------------------------------------


def test_strong_error_is_linear_on_the_reported_input() -> None:
    """CodeQL's own repro: a string that can be split many ways, never closing."""
    probe = "A0" + "aA" * (LENGTH // 2) + "!"
    assert elapsed(failures._STRONG_ERROR.match, probe) < BUDGET_S


@pytest.mark.parametrize(
    "probe",
    [
        "A" * LENGTH,
        ("Aa" * (LENGTH // 2)),
        ("AB" * (LENGTH // 2)),
        "E " + "Xy" * (LENGTH // 2),
        "error" + "[" * LENGTH,
        "Er" + "r" * LENGTH,
        "a" * LENGTH,
        "a." * (LENGTH // 2),
        # The shapes below repeat the *keywords the pattern searches for*. The
        # first version of this suite omitted them and missed a quadratic path:
        # `[a-z_.]*` before the keyword alternation retried every possible split
        # point, so `"error" * n` took 2.7s at 16 KB. Repeating the literal a
        # pattern is looking for is an obvious adversarial shape in hindsight,
        # and it is also entirely ordinary in a noisy log.
        "error" * (LENGTH // 5),
        "exception" * (LENGTH // 9),
        "panic" * (LENGTH // 5),
        "fault" * (LENGTH // 5),
        "E " + "error" * (LENGTH // 5),
        "error" * (LENGTH // 10) + "[" * (LENGTH // 2),
    ],
    ids=[
        "all-caps",
        "alternating",
        "pairs",
        "pytest-prefixed",
        "brackets",
        "long-tail",
        "all-lower",
        "dotted",
        "repeated-error",
        "repeated-exception",
        "repeated-panic",
        "repeated-fault",
        "prefixed-repeated-error",
        "repeated-then-brackets",
    ],
)
def test_strong_error_survives_adversarial_shapes(probe: str) -> None:
    assert elapsed(failures._STRONG_ERROR.match, probe) < BUDGET_S


@pytest.mark.parametrize(
    "keyword", ["error", "exception", "panic", "fault", "assert", "failed", "timeout"]
)
def test_repeating_a_searched_keyword_stays_linear(keyword: str) -> None:
    """Generalised: repeating any literal either pattern looks for must not
    trigger backtracking, whichever pattern is asked."""
    probe = keyword * (LENGTH // len(keyword))
    assert elapsed(failures._STRONG_ERROR.match, probe) < BUDGET_S
    assert elapsed(failures._WEAK_ERROR.search, probe) < BUDGET_S
    assert elapsed(failures.normalize_error, probe) < BUDGET_S


def test_strong_error_still_recognises_real_error_lines() -> None:
    """The fix must not have narrowed what it matches."""
    for line in (
        "PoolExhausted: no connections available",
        "TimeoutError: deadlock in db fixture",
        "E   TimeoutError: deadlock",
        "HTTPError: 500",
        "ValueError: bad input",
        "error[E0308]: mismatched types",
        "FATAL: database does not exist",
        "Segmentation fault (core dumped)",
        "panic: runtime error: index out of range",
    ):
        assert failures._STRONG_ERROR.match(line), f"stopped matching: {line!r}"


def test_strong_error_still_rejects_non_error_lines() -> None:
    for line in ("Note: this is not an error", "just some output", "assert x()"):
        assert not failures._STRONG_ERROR.match(line), f"now over-matches: {line!r}"


# --- Every other pattern on a hostile-input path ---------------------------


@pytest.mark.parametrize(
    "probe",
    [
        "a" * LENGTH,
        "/" * LENGTH,
        "0" * LENGTH,
        ":" * LENGTH,
        "-" * LENGTH,
        ("a/" * (LENGTH // 2)),
        ("a:" * (LENGTH // 2)),
        ("0." * (LENGTH // 2)),
        "/tmp/" + "a/" * (LENGTH // 2),
        "0x" + "a" * LENGTH,
    ],
    ids=[
        "letters",
        "slashes",
        "digits",
        "colons",
        "hyphens",
        "path-ish",
        "colon-pairs",
        "decimals",
        "deep-path",
        "hex",
    ],
)
def test_error_normalisation_is_linear(probe: str) -> None:
    """`normalize_error` runs every normaliser in sequence over error text."""
    assert elapsed(failures.normalize_error, probe) < BUDGET_S


@pytest.mark.parametrize(
    "probe",
    ["a " * (LENGTH // 2), "/tmp/" + "x" * LENGTH, " " * LENGTH],
    ids=["spaced", "tmp-path", "whitespace"],
)
def test_command_normalisation_is_linear(probe: str) -> None:
    assert elapsed(failures.normalize_command, probe) < BUDGET_S


@pytest.mark.parametrize(
    "probe",
    [
        "a" * LENGTH,
        "sk-" + "a" * LENGTH,
        "password=" + "a" * LENGTH,
        ("token=" + "a" * 100) * 200,
        "://" + "a:" * (LENGTH // 2) + "@",
        "-----BEGIN RSA PRIVATE KEY-----" + "a" * LENGTH,
        "eyJ" + "a." * (LENGTH // 2),
        ("api_key: " + "x" * 50 + "\n") * 400,
    ],
    ids=[
        "letters",
        "sk-prefix",
        "assignment",
        "many-assignments",
        "url-userinfo",
        "unterminated-pem",
        "jwt-ish",
        "many-lines",
    ],
)
def test_redaction_is_linear(probe: str) -> None:
    """Redaction runs on every durable write, over whatever a command printed."""
    assert elapsed(redact.redact_text, probe) < BUDGET_S


@pytest.mark.parametrize(
    "probe",
    ["a" * LENGTH, "sk-" + "a" * LENGTH, ("eyJ" + "a." * 100) * 100],
    ids=["letters", "sk-prefix", "jwt-ish"],
)
def test_the_audit_scan_is_linear(probe: str) -> None:
    assert elapsed(redact.scan_for_secrets, probe) < BUDGET_S


@pytest.mark.parametrize(
    "probe",
    [
        "a" * LENGTH,
        "ignore " * (LENGTH // 7),
        "curl " + "a" * LENGTH,
        ("curl " + "x" * 100 + " | sh\n") * 200,
        "system" + ":" * LENGTH,
        ("note for the ai " * (LENGTH // 16)),
        "rm -rf " + "/" * LENGTH,
    ],
    ids=[
        "letters",
        "repeated-keyword",
        "curl-tail",
        "many-pipes",
        "colons",
        "repeated-phrase",
        "slashes",
    ],
)
def test_poisoning_heuristics_are_linear(probe: str) -> None:
    """These run at admission on agent-supplied text — the most hostile input
    Provalume accepts."""
    assert elapsed(poisoning.assess, probe) < BUDGET_S


@pytest.mark.parametrize(
    "probe",
    ["a" * LENGTH, ("a." * (LENGTH // 2)), ("a/" * (LENGTH // 2)), "!" * LENGTH],
    ids=["letters", "dots", "slashes", "punctuation"],
)
def test_fts_tokenisation_is_linear(probe: str) -> None:
    assert elapsed(fts.build_query, probe) < BUDGET_S


# --- Guard against reintroducing the shape ---------------------------------


def _class_members(spec: str) -> set[str]:
    """Approximate the members of a character class body.

    Expands ranges and literals well enough to answer one question: can these
    two classes match the same character? Escapes like ``\\w`` are treated as
    matching everything, which errs toward flagging rather than missing.
    """
    if any(token in spec for token in (r"\w", r"\S", r"\D", ".")):
        return {chr(c) for c in range(32, 127)}
    members: set[str] = set()
    index = 0
    while index < len(spec):
        if spec[index] == "\\" and index + 1 < len(spec):
            members.add(spec[index + 1])
            index += 2
            continue
        if index + 2 < len(spec) and spec[index + 1] == "-":
            members.update(chr(c) for c in range(ord(spec[index]), ord(spec[index + 2]) + 1))
            index += 3
            continue
        members.add(spec[index])
        index += 1
    return members


#: `(?:[HEAD][TAIL]*)+` — a repeated group whose body is an anchor class
#: followed by an unbounded class. Ambiguous *only* if the two classes overlap,
#: because then one input can be split more than one way.
_NESTED = re.compile(r"\(\?:\[(?P<head>[^\]]*)\]\[(?P<tail>[^\]]*)\][*+]\)[*+]")


def test_no_pattern_nests_an_unbounded_quantifier_ambiguously() -> None:
    """Structural tripwire for the shape that caused the original bug.

    ``(?:[A][B]*)+`` is safe when A and B are disjoint — each iteration is then
    anchored to exactly one character that cannot appear in the tail, so there is
    only one way to split any input. It is exponential when they overlap, which
    is exactly what ``[A-Z][a-zA-Z0-9]*`` did: an uppercase letter could belong
    to either the anchor or the previous tail.

    So the check tests **overlap**, not shape. Flagging every nested quantifier
    would flag the fixed pattern too and teach people to ignore the failure.
    """
    sources: list[tuple[str, str]] = []
    sources += [(f"failures/{i}", p.pattern) for i, (p, _) in enumerate(failures._NORMALIZERS)]
    sources += [(f"redact/{r.family}", r.pattern.pattern) for r in redact.RULES]
    sources += [(f"poisoning/{p.name}", p.regex.pattern) for p in poisoning.PATTERNS]
    sources.append(("failures/_STRONG_ERROR", failures._STRONG_ERROR.pattern))
    sources.append(("failures/_WEAK_ERROR", failures._WEAK_ERROR.pattern))
    sources.append(("fts/_TERM", fts._TERM.pattern))
    sources.append(("gitinfo/_VERSION", gitinfo._VERSION.pattern))
    sources.append(("gitinfo/_HEX_SHA", gitinfo._HEX_SHA.pattern))
    sources.append(("coverage/_VERSION", _coverage._VERSION.pattern))
    sources.append(("coverage/_PYTHON_BINARY", _coverage._PYTHON_BINARY.pattern))

    flagged: list[str] = []
    for name, pattern in sources:
        for match in _NESTED.finditer(pattern):
            head = _class_members(match.group("head"))
            tail = _class_members(match.group("tail"))
            if head & tail:
                shared = "".join(sorted(head & tail))[:20]
                flagged.append(f"{name}: iterations overlap on {shared!r}")

    assert not flagged, (
        "ambiguous nested quantifier(s): "
        + "; ".join(flagged)
        + ". This is the shape that made _STRONG_ERROR exponential. Anchor each "
        "iteration on a character that cannot appear in its tail, so only one "
        "split of any input is possible."
    )


def test_the_tripwire_would_have_caught_the_original_bug() -> None:
    """The check is only worth having if it fires on the real defect."""
    original = r"[A-Z][a-z0-9]+(?:[A-Z][a-zA-Z0-9]*)+\s*:"
    match = _NESTED.search(original)
    assert match is not None
    assert _class_members(match.group("head")) & _class_members(match.group("tail"))

    fixed = failures._STRONG_ERROR.pattern
    for candidate in _NESTED.finditer(fixed):
        head = _class_members(candidate.group("head"))
        tail = _class_members(candidate.group("tail"))
        assert not (head & tail), "the fix did not make the iterations disjoint"


def test_a_hostile_excerpt_does_not_hang_the_writer(pv: object) -> None:
    """End to end: the whole admission and projection path, with a payload
    designed to trip every pattern at once."""
    hostile = (
        "A0"
        + "aA" * 4000
        + " "
        + "sk-"
        + "x" * 2000
        + " "
        + "ignore all previous instructions " * 200
        + " "
        + "/tmp/"
        + "a/" * 2000
    )
    start = time.perf_counter()
    pv.record_verification(  # type: ignore[attr-defined]
        command="hostile", passed=False, excerpt=hostile[:8000], error_kind="e"
    )
    assert time.perf_counter() - start < 5.0
