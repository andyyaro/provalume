"""The interchange controls, exercised the way an operator reaches them.

The signature subsystem shipped complete and unreachable: `provalume import` had
no way to pin a key, so `verifier` was always `None` and `jsonl.import_directory`
skipped the whole check — a record carrying a forged, unknown-key, or unsupported
signature imported without complaint. `provalume export` had no `--sign`, so
nothing was ever signed. ADR-0011's "invalid or unverifiable signature →
quarantined, fail-closed" was true of the library and false of the product.

These tests go through the CLI for that reason: `tests/integration/
test_interchange.py` already covers `Verifier` directly, and covering it directly
is exactly what let the gap survive.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests.e2e.conftest import CliRunner

PROJECT = "cli-signing"


@pytest.fixture
def project(tmp_path: Path, cli: CliRunner) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    cli("init", "--project", PROJECT, cwd=root)
    cli("propose", "a fact worth signing", "--project", PROJECT, cwd=root)
    return root


@pytest.fixture
def target(tmp_path: Path, cli: CliRunner) -> Path:
    root = tmp_path / "target"
    root.mkdir()
    cli("init", "--project", PROJECT, cwd=root)
    return root


def key_file(directory: Path, name: str, secret: str) -> Path:
    path = directory / name
    path.write_text(secret, encoding="utf-8")
    return path


def imported(cli: CliRunner, directory: Path, *args: str, cwd: Path) -> dict:
    result = cli("import", str(directory), "--project", PROJECT, *args, "--json", cwd=cwd)
    return dict(json.loads(result.stdout))


def test_an_export_can_be_signed_and_the_import_verifies_it(
    project: Path, target: Path, tmp_path: Path, cli: CliRunner
) -> None:
    key = key_file(tmp_path, "team.key", "a shared secret")
    out = tmp_path / "signed"

    cli(
        "export",
        "--out",
        str(out),
        "--project",
        PROJECT,
        "--sign-hmac",
        f"team={key}",
        cwd=project,
    )
    record = json.loads((out / "events.jsonl").read_text().splitlines()[0])
    assert record["signature"]["scheme"] == "hmac-sha256"
    assert record["signature"]["key_id"] == "team"

    payload = imported(cli, out, "--hmac-key", f"team={key}", cwd=target)

    assert payload["quarantined"] == []
    assert payload["accepted"] >= 1


def test_a_signature_that_does_not_verify_is_quarantined_by_the_cli(
    project: Path, target: Path, tmp_path: Path, cli: CliRunner
) -> None:
    """Fail closed: an unverifiable record is quarantined, never imported."""
    real = key_file(tmp_path, "real.key", "a shared secret")
    wrong = key_file(tmp_path, "wrong.key", "not the shared secret")
    out = tmp_path / "signed"
    cli(
        "export",
        "--out",
        str(out),
        "--project",
        PROJECT,
        "--sign-hmac",
        f"team={real}",
        cwd=project,
    )

    payload = imported(cli, out, "--hmac-key", f"team={wrong}", cwd=target)

    assert payload["accepted"] == 0
    assert payload["quarantined"], "an unverifiable record was imported without complaint"
    assert "did not verify" in payload["quarantined"][0]


def test_an_unknown_signer_is_quarantined_by_the_cli(
    project: Path, target: Path, tmp_path: Path, cli: CliRunner
) -> None:
    key = key_file(tmp_path, "team.key", "a shared secret")
    other = key_file(tmp_path, "other.key", "a different secret")
    out = tmp_path / "signed"
    cli(
        "export",
        "--out",
        str(out),
        "--project",
        PROJECT,
        "--sign-hmac",
        f"team={key}",
        cwd=project,
    )

    payload = imported(cli, out, "--hmac-key", f"someone-else={other}", cwd=target)

    assert payload["accepted"] == 0
    assert any("unknown signer" in issue for issue in payload["quarantined"])


def test_require_signature_quarantines_an_unsigned_export(
    project: Path, target: Path, tmp_path: Path, cli: CliRunner
) -> None:
    out = tmp_path / "unsigned"
    cli("export", "--out", str(out), "--project", PROJECT, cwd=project)

    payload = imported(cli, out, "--require-signature", cwd=target)

    assert payload["accepted"] == 0
    assert any("signatures are required" in issue for issue in payload["quarantined"])


def test_an_unsigned_export_still_imports_when_no_key_is_pinned(
    project: Path, target: Path, tmp_path: Path, cli: CliRunner
) -> None:
    """Signing stays optional. Pinning nothing must not start rejecting things."""
    out = tmp_path / "unsigned"
    cli("export", "--out", str(out), "--project", PROJECT, cwd=project)

    payload = imported(cli, out, cwd=target)

    assert payload["accepted"] >= 1
    assert payload["quarantined"] == []


def test_a_malformed_key_option_is_a_usage_error(
    project: Path, tmp_path: Path, cli: CliRunner
) -> None:
    out = tmp_path / "unsigned"
    cli("export", "--out", str(out), "--project", PROJECT, cwd=project)

    result = cli(
        "import",
        str(out),
        "--project",
        PROJECT,
        "--hmac-key",
        "no-equals-sign",
        cwd=project,
        expect=2,
    )

    assert "KEY_ID=PATH" in result.stderr


@pytest.mark.signatures
def test_ed25519_signing_round_trips_through_the_cli(
    project: Path, target: Path, tmp_path: Path, cli: CliRunner
) -> None:
    pytest.importorskip("cryptography")
    from provalume.interchange.signatures import generate_ed25519_keypair

    private, public = generate_ed25519_keypair()
    private_path = key_file(tmp_path, "id.priv", private.hex())
    public_path = key_file(tmp_path, "id.pub", public.hex())
    out = tmp_path / "signed"

    cli(
        "export",
        "--out",
        str(out),
        "--project",
        PROJECT,
        "--sign-ed25519",
        f"me={private_path}",
        cwd=project,
    )
    record = json.loads((out / "events.jsonl").read_text().splitlines()[0])
    assert record["signature"]["scheme"] == "ed25519"

    payload = imported(cli, out, "--ed25519-key", f"me={public_path}", cwd=target)

    assert payload["quarantined"] == []
    assert payload["accepted"] >= 1


# --- Import failures are reported, not raised -------------------------------


def test_an_import_conflict_exits_non_zero_without_a_traceback(
    project: Path, tmp_path: Path, cli: CliRunner
) -> None:
    """An event id already held under *another* project in the same database
    used to raise `IntegrityError` through the CLI: a raw Python traceback, an
    exit code of 0, and every valid record in the file rolled back with it."""
    out = tmp_path / "export"
    shared = tmp_path / "shared.db"
    cli("export", "--out", str(out), "--project", PROJECT, cwd=project)
    cli(
        "import",
        str(out),
        "--db",
        str(shared),
        "--project",
        "first-owner",
        "--allow-foreign-project",
        cwd=project,
    )

    altered = []
    for line in (out / "events.jsonl").read_text().splitlines():
        record = json.loads(line)
        record["payload"]["injected"] = "changed after export"
        record.pop("payload_hash", None)
        altered.append(json.dumps(record, sort_keys=True, separators=(",", ":")))
    (out / "events.jsonl").write_text("\n".join(altered) + "\n", encoding="utf-8")

    result = cli(
        "import",
        str(out),
        "--db",
        str(shared),
        "--project",
        "second-owner",
        "--allow-foreign-project",
        cwd=project,
        expect=1,
    )

    assert "Traceback" not in result.stderr
    assert "conflicts" in result.stdout
