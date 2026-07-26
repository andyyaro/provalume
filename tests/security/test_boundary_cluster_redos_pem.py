"""The PEM redaction rule, on the shape `tests/security/test_redos.py` missed.

That suite's own comment records the lesson — "repeat the literal a pattern is
looking for" — and applies it to the error normaliser, but its PEM probe is a
*single* unterminated BEGIN marker, which is linear either way. Repeat the
marker and the old `-----BEGIN … -----.*?-----END … -----` with DOTALL is clean
O(n^2): every BEGIN position rescans to the end of the input looking for an END
that is not there. Measured at 0.109s / 1.208s / 11.205s for 20 KB / 80 KB /
250 KB. Redaction runs on every durable write, over whatever a failing command
printed (threat T24).
"""

from __future__ import annotations

import time

import pytest

from provalume import redact

#: Same budget as the existing ReDoS suite. The quadratic version took 11s at
#: 250 KB, so there is no risk of flakiness on a slow runner.
BUDGET_S = 2.0
LENGTH = 250_000

MARKER = "-----BEGIN PRIVATE KEY-----"


def elapsed(fn: object, *args: object) -> float:
    start = time.perf_counter()
    fn(*args)  # type: ignore[operator]
    return time.perf_counter() - start


@pytest.mark.parametrize(
    "probe",
    [
        MARKER * (LENGTH // len(MARKER)),
        "-----BEGIN RSA PRIVATE KEY-----" * (LENGTH // 31),
        (MARKER + "\n" + "MIIEpAIBAAKCAQEA" * 4 + "\n") * (LENGTH // 90),
        MARKER + "a" * LENGTH,
        ("-----END PRIVATE KEY-----" * (LENGTH // 25)),
        "-" * LENGTH,
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
def test_pem_redaction_is_linear_on_repeated_markers(probe: str) -> None:
    assert elapsed(redact.redact_text, probe) < BUDGET_S


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
