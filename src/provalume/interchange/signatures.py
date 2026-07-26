"""Optional cryptographic signatures for interchange records (ADR-0011).

Two schemes, with an honest difference between them:

``hmac-sha256``
    Standard library only. Proves the signer held a shared secret. Anyone with
    that secret can forge, so it authenticates a *channel*, not a person.

``ed25519``
    Requires ``provalume[signatures]``. Proves the holder of a specific private
    key produced it.

Both **fail closed**. If ``cryptography`` is absent, an Ed25519-signed record is
quarantined with an explicit reason rather than accepted unverified (threat T18).
An unknown signer is an untrusted signer.

The thing to keep straight: **a valid signature proves origin, never
truthfulness.** A signed lie is a verified-origin lie. Signatures raise confidence
about *who* wrote a record, and change nothing about whether its content is
correct — which is why an imported record's trust is still re-derived locally from
evidence, regardless of how well it is signed.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Final

from provalume.errors import SignatureError
from provalume.interchange.hashing import canonical_bytes

SCHEME_HMAC: Final = "hmac-sha256"
SCHEME_ED25519: Final = "ed25519"

SUPPORTED_SCHEMES: Final[frozenset[str]] = frozenset({SCHEME_HMAC, SCHEME_ED25519})


def ed25519_available() -> bool:
    """Whether the optional Ed25519 backend is installed."""
    try:
        import cryptography.hazmat.primitives.asymmetric.ed25519  # noqa: F401
    except ImportError:
        return False
    return True


def _signable(record: dict[str, Any]) -> bytes:
    """Canonical bytes to sign.

    The ``signature`` field is excluded, since it cannot be part of its own
    input. Everything else is included: signing a subset would let an attacker
    alter the unsigned remainder while the signature still verified.
    """
    material = {k: v for k, v in record.items() if k != "signature"}
    return canonical_bytes(material)


# --- HMAC ------------------------------------------------------------------


def sign_hmac(record: dict[str, Any], *, key: bytes, key_id: str = "") -> dict[str, Any]:
    """Sign with HMAC-SHA256. Returns the signature block, not the record."""
    if not key:
        msg = "an HMAC key is required"
        raise SignatureError(msg)
    digest = hmac.new(key, _signable(record), hashlib.sha256).hexdigest()
    return {"scheme": SCHEME_HMAC, "key_id": key_id, "value": digest}


def verify_hmac(record: dict[str, Any], signature: dict[str, Any], *, key: bytes) -> bool:
    """Verify an HMAC signature in constant time."""
    if signature.get("scheme") != SCHEME_HMAC:
        return False
    expected = hmac.new(key, _signable(record), hashlib.sha256).hexdigest()
    # compare_digest, not ==, so verification time does not leak how much of the
    # digest matched.
    return hmac.compare_digest(expected, str(signature.get("value", "")))


# --- Ed25519 ---------------------------------------------------------------


def generate_ed25519_keypair() -> tuple[bytes, bytes]:
    """Generate ``(private_bytes, public_bytes)`` as raw 32-byte values."""
    if not ed25519_available():
        msg = (
            "Ed25519 signing requires the optional dependency. "
            "Install with: pip install 'provalume[signatures]'"
        )
        raise SignatureError(msg)
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    private = ed25519.Ed25519PrivateKey.generate()
    return (
        private.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ),
    )


def sign_ed25519(record: dict[str, Any], *, private_key: bytes, key_id: str = "") -> dict[str, Any]:
    """Sign with Ed25519."""
    if not ed25519_available():
        msg = (
            "Ed25519 signing requires the optional dependency. "
            "Install with: pip install 'provalume[signatures]'"
        )
        raise SignatureError(msg)
    from cryptography.hazmat.primitives.asymmetric import ed25519

    key = ed25519.Ed25519PrivateKey.from_private_bytes(private_key)
    return {
        "scheme": SCHEME_ED25519,
        "key_id": key_id,
        "value": key.sign(_signable(record)).hex(),
    }


def verify_ed25519(record: dict[str, Any], signature: dict[str, Any], *, public_key: bytes) -> bool:
    """Verify an Ed25519 signature.

    Raises rather than returning ``False`` when the backend is missing. The
    distinction matters: "the signature is invalid" and "I cannot check this
    signature" are different facts, and collapsing them would let a missing
    dependency read as a forgery — or worse, get handled by a caller that treats
    ``False`` as "skip it and continue".
    """
    if signature.get("scheme") != SCHEME_ED25519:
        return False
    if not ed25519_available():
        msg = (
            "record is Ed25519-signed but the verification backend is not installed, "
            "so its origin cannot be checked. Quarantining rather than trusting. "
            "Install with: pip install 'provalume[signatures]'"
        )
        raise SignatureError(msg)
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric import ed25519

    try:
        key = ed25519.Ed25519PublicKey.from_public_bytes(public_key)
        key.verify(bytes.fromhex(str(signature.get("value", ""))), _signable(record))
    except (InvalidSignature, ValueError):
        return False
    return True


# --- Dispatch --------------------------------------------------------------


class Verifier:
    """Verifies signatures against a pinned set of keys.

    Keys must be pinned in advance. A record carrying its own public key would be
    self-authenticating, which is not authentication at all — an attacker would
    simply supply a key they hold.
    """

    def __init__(
        self,
        *,
        hmac_keys: dict[str, bytes] | None = None,
        ed25519_keys: dict[str, bytes] | None = None,
        require_signature: bool = False,
    ) -> None:
        self.hmac_keys = hmac_keys or {}
        self.ed25519_keys = ed25519_keys or {}
        self.require_signature = require_signature

    def verify(self, record: dict[str, Any]) -> tuple[bool, str]:
        """Verify a record's signature.

        Returns ``(ok, reason)``. ``reason`` is always populated — on success it
        says which key verified, which is what makes an import audit meaningful.

        Every failure path returns ``False``. There is no path that returns
        ``True`` because verification could not be performed.
        """
        signature = record.get("signature")

        if signature is None:
            if self.require_signature:
                return False, "unsigned, and signatures are required by configuration"
            return True, "unsigned (signatures not required)"

        if not isinstance(signature, dict):
            return False, "signature field is not an object"

        scheme = str(signature.get("scheme", ""))
        key_id = str(signature.get("key_id", ""))

        if scheme not in SUPPORTED_SCHEMES:
            return False, f"unsupported signature scheme {scheme!r}"

        if scheme == SCHEME_HMAC:
            key = self.hmac_keys.get(key_id)
            if key is None:
                return False, f"no pinned HMAC key for key_id {key_id!r}; unknown signer"
            if verify_hmac(record, signature, key=key):
                return True, f"HMAC-SHA256 signature valid for key_id {key_id!r}"
            return False, f"HMAC signature did not verify for key_id {key_id!r}"

        public = self.ed25519_keys.get(key_id)
        if public is None:
            return False, f"no pinned Ed25519 key for key_id {key_id!r}; unknown signer"
        try:
            ok = verify_ed25519(record, signature, public_key=public)
        except SignatureError as exc:
            return False, str(exc)
        if ok:
            return True, f"Ed25519 signature valid for key_id {key_id!r}"
        return False, f"Ed25519 signature did not verify for key_id {key_id!r}"
