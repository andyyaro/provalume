"""Redaction runs before the durable write, and audit proves it (threat T11)."""

from __future__ import annotations

import json

import pytest

from provalume import redact
from provalume.sdk.client import Provalume

REAL_SHAPES = [
    ("anthropic", "sk-ant-api03-abcdefghijklmnopqrstuvwxyz"),
    ("openai", "sk-proj-abcdefghijklmnopqrstuvwxyz123456"),
    ("github-classic", "ghp_1234567890abcdefghijABCDEFGHIJKLMN"),
    ("github-pat", "github_pat_11ABCDEFG0abcdefghij_klmnopqrstuvwxyz"),
    ("gitlab", "glpat-abcdefghij1234567890"),
    ("google", "AIzaSyD-1234567890abcdefghijklmnopqrstuv"),
    ("aws-key-id", "AKIAIOSFODNN7EXAMPLE"),
    ("slack-bot", "xoxb-1234567890-abcdefghijklm"),
    ("npm", "npm_abcdefghij1234567890ABCDEFGHIJ"),
    ("pypi", "pypi-AgEIcHlwaS5vcmcABCDEFGH"),
    ("jwt", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcdefghij"),
]


@pytest.mark.parametrize(("name", "secret"), REAL_SHAPES)
def test_known_credential_shapes_are_redacted(name: str, secret: str) -> None:
    out, report = redact.redact_text(f"the value is {secret} here")
    assert secret not in out, f"{name} survived redaction"
    assert report.applied
    assert not redact.scan_for_secrets(out)


def test_url_userinfo_keeps_context_and_drops_the_password() -> None:
    out, _ = redact.redact_text("postgres://admin:hunter2@db.internal:5432/app")
    assert "hunter2" not in out
    assert "admin" in out, "the non-secret username should survive"
    assert "db.internal" in out, "the host should survive"


def test_pem_private_key_block_is_removed() -> None:
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA1234567890\n-----END RSA PRIVATE KEY-----"
    )
    out, _ = redact.redact_text(f"key:\n{pem}\ndone")
    assert "MIIEpAIBAAKCAQEA" not in out
    assert not redact.scan_for_secrets(out)


def test_generic_assignment_is_redacted() -> None:
    out, _ = redact.redact_text("DATABASE_PASSWORD=s3cr3tvalue")
    assert "s3cr3tvalue" not in out


def test_key_casing_and_spacing_survive_redaction() -> None:
    """Only the value is replaced; rewriting the key would edit text that was
    not secret."""
    out, _ = redact.redact_text("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIbPxRfiCYEXAMPLEKEY")
    assert out.startswith("AWS_SECRET_ACCESS_KEY=")
    assert "wJalrXUtnFEMI" not in out


@pytest.mark.parametrize(
    "benign",
    [
        "STATUS=required",
        "token=true",
        "api_key: '${MY_KEY}'",
        "TOKEN_PATH=/etc/creds/token",
        "password: null",
        "secret=unset",
        "auth_key = {{ vault_key }}",
        "credential: missing",
    ],
)
def test_benign_values_are_not_over_redacted(benign: str) -> None:
    """False positives are acceptable but not free — this rule set would redact
    half of every log line without the placeholder and status-word lookahead."""
    out, report = redact.redact_text(benign)
    assert not report.applied, f"over-redacted a benign value: {benign!r} -> {out!r}"


def test_structured_redaction_catches_shapeless_secrets() -> None:
    """A short opaque password under an obviously-secret key slips past every
    pattern rule; the key-based pass is what catches it."""
    payload = {"env": {"PASSWORD": "abc"}, "nested": [{"client_secret": "xy"}]}
    out, report = redact.redact_structured(payload)
    assert out["env"]["PASSWORD"] == redact.REDACTED
    assert out["nested"][0]["client_secret"] == redact.REDACTED
    assert "sensitive-key" in report.families


def test_empty_sensitive_values_are_left_alone() -> None:
    out, _ = redact.redact_structured({"password": "", "token": None})
    assert out["password"] == ""
    assert out["token"] is None


def test_redaction_metadata_distinguishes_clean_from_cleaned() -> None:
    _, clean = redact.redact_text("nothing secret here")
    _, cleaned = redact.redact_text("ghp_1234567890abcdefghijABCDEFGHIJKLMN")
    assert not clean.applied
    assert cleaned.applied and cleaned.matches >= 1
    assert "github" in cleaned.families


def test_metadata_never_contains_the_secret() -> None:
    secret = "ghp_1234567890abcdefghijABCDEFGHIJKLMN"
    _, report = redact.redact_text(secret)
    assert secret not in json.dumps(report.to_dict())


# --- End to end through the SDK --------------------------------------------


def test_secrets_never_reach_durable_storage(pv: Provalume) -> None:
    pv.record_verification(
        command="deploy --token ghp_1234567890abcdefghijABCDEFGHIJKLMN",
        passed=False,
        excerpt="AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIbPxRfiCYEXAMPLEKEY rejected",
        error_kind="auth_error",
    )
    for event in pv.journal.iter_all():
        raw = json.dumps(event.payload)
        assert not redact.scan_for_secrets(raw), f"secret persisted in {event.event_id}"
    for memory in pv.memories.iter_all():
        assert not redact.scan_for_secrets(memory.text + json.dumps(memory.content))


def test_hashes_cover_redacted_content(pv: Provalume) -> None:
    """Hashing happens after redaction, so a stored hash attests to what is
    actually on disk rather than to the unredacted original."""
    from provalume.interchange.hashing import hash_payload

    pv.record_verification(
        command="x --token ghp_1234567890abcdefghijABCDEFGHIJKLMN",
        passed=False,
        excerpt="denied",
        error_kind="e",
    )
    for event in pv.journal.iter_all():
        assert event.payload_hash == hash_payload(event.payload)


def test_audit_reports_a_clean_credential_scan(pv: Provalume) -> None:
    pv.record_verification(
        command="deploy", passed=False, excerpt="sk-ant-api03-abcdefghijklmnop", error_kind="e"
    )
    report = pv.audit(deep=True)
    assert not [f for f in report.errors if f.check == "credential_scan"]


def test_export_refuses_when_audit_finds_a_leak(pv: Provalume, tmp_path) -> None:
    """Export is the last place to catch a leak, and the worst place to
    discover one later."""
    from provalume.errors import ProvalumeError

    pv.record_verification(command="ok", passed=True)
    # Force a leak past redaction by writing straight to the projection table,
    # simulating a redaction rule that failed to match.
    with pv.db.tx() as conn:
        conn.execute(
            "UPDATE memories SET text = ? WHERE 1",
            ("leaked ghp_1234567890abcdefghijABCDEFGHIJKLMN",),
        )
    with pytest.raises(ProvalumeError, match="credential"):
        pv.export(tmp_path / "out")


def test_scan_is_honest_about_what_it_cannot_find() -> None:
    """A clean scan proves no *known pattern* matched, not that no secret is
    present. Documented in PRIVACY_MODEL.md §3 so users rotate rather than
    trust a clean audit."""
    assert redact.scan_for_secrets("password is correcthorsebatterystaple") == []
