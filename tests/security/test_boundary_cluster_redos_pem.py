"""The PEM redaction rule, on the shape `tests/security/test_redos.py` missed.

That suite's own comment records the lesson - "repeat the literal a pattern is
looking for" - and applies it to the error normaliser, but its PEM probe is a
*single* unterminated BEGIN marker, which is linear either way. Repeat the
marker and the old `-----BEGIN … -----.*?-----END … -----` with DOTALL is clean
O(n^2): every BEGIN position rescans to the end of the input looking for an END
that is not there. Measured at 0.109s / 1.208s / 11.205s for 20 KB / 80 KB /
250 KB. Redaction runs on every durable write, over whatever a failing command
printed (threat T24).
"""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest

from provalume import redact

#: The probe sizes whose costs are compared. The assertion is about the *ratio*
#: between them, so what matters is that the smaller one is comfortably above
#: timer noise, not that either is large in absolute terms.
SMALL = 64_000
GROWTH = 4

#: Linear work grows by ~GROWTH between the two sizes; the quadratic version
#: grew by ~GROWTH**2, i.e. 16x. The threshold sits at the geometric mean of the
#: two, so it is as far from a passing linear curve as from a failing quadratic
#: one, and no runner's speed enters into it.
MAX_GROWTH_RATIO = 8.0

#: Each size is timed this many times and the *minimum* is kept. Scheduling
#: noise on a shared runner only ever adds time, so the minimum is the closest
#: estimate of the real cost -- and it is the reason this test does not need a
#: wall-clock budget to be stable (issue #13).
REPEATS = 3

MARKER = "-----BEGIN PRIVATE KEY-----"
RSA_MARKER = "-----BEGIN RSA PRIVATE KEY-----"
END_MARKER = "-----END PRIVATE KEY-----"
BODY_UNIT = MARKER + "\n" + "MIIEpAIBAAKCAQEA" * 4 + "\n"


def elapsed(fn: object, *args: object) -> float:
    start = time.perf_counter()
    fn(*args)  # type: ignore[operator]
    return time.perf_counter() - start


def best_of(repeats: int, fn: object, *args: object) -> float:
    return min(elapsed(fn, *args) for _ in range(repeats))


@pytest.mark.parametrize(
    "probe",
    [
        lambda n: MARKER * (n // len(MARKER)),
        lambda n: RSA_MARKER * (n // len(RSA_MARKER)),
        lambda n: BODY_UNIT * (n // len(BODY_UNIT)),
        lambda n: MARKER + "a" * n,
        lambda n: END_MARKER * (n // len(END_MARKER)),
        lambda n: "-" * n,
    ],
    ids=[
        "repeated-begin",
        "repeated-begin-rsa",
        "repeated-begin-with-body",
        "single-unterminated",
        "repeated-end",
        "hyphens",
    ],
)
def test_pem_redaction_is_linear_on_repeated_markers(probe: Callable[[int], str]) -> None:
    """Assert the growth curve, not the wall clock.

    A fixed budget measured how fast the runner was as much as how fast the
    redactor was: 2.0s held on Linux and failed intermittently on macOS at
    2.5-4.4s for byte-identical code (issue #13). Quadratic-vs-linear is a
    property of the pattern, so comparing the same probe at two sizes tests it
    directly and cancels the machine out of the result.
    """
    small = best_of(REPEATS, redact.redact_text, probe(SMALL))
    large = best_of(REPEATS, redact.redact_text, probe(SMALL * GROWTH))

    ratio = large / max(small, 1e-9)
    assert ratio < MAX_GROWTH_RATIO, (
        f"{GROWTH}x the input cost {ratio:.1f}x the time "
        f"({small * 1000:.1f}ms -> {large * 1000:.1f}ms); "
        f"linear is ~{GROWTH}x and the quadratic regression was ~{GROWTH**2}x"
    )


def test_a_pem_private_key_is_still_removed() -> None:
    """The bound must not have narrowed what the rule matches."""
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA1234567890\n-----END RSA PRIVATE KEY-----"
    )
    out, report = redact.redact_text(f"key:\n{pem}\ndone")

    assert "MIIEpAIBAAKCAQEA" not in out
    assert "pem" in report.families
    assert not redact.scan_for_secrets(out)


def test_an_encrypted_pem_header_does_not_stop_the_match() -> None:
    """A traditional encrypted key carries `Proc-Type:` and `DEK-Info:` lines
    before the body, and their single hyphens have to stay matchable."""
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "Proc-Type: 4,ENCRYPTED\n"
        "DEK-Info: DES-EDE3-CBC,0123456789ABCDEF\n"
        "\n"
        "MIIEpAIBAAKCAQEA1234567890\n"
        "-----END RSA PRIVATE KEY-----"
    )
    out, _ = redact.redact_text(pem)

    assert "MIIEpAIBAAKCAQEA" not in out


def test_an_ec_private_key_is_still_removed() -> None:
    out, _ = redact.redact_text(
        "-----BEGIN EC PRIVATE KEY-----\nMHcCAQEEIB\n-----END EC PRIVATE KEY-----"
    )

    assert "MHcCAQEEIB" not in out
