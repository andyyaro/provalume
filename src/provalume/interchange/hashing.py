"""Canonical JSON serialisation and deterministic hashing.

Everything Provalume claims about provenance rests on this module. A hash that
varies with dictionary insertion order, float formatting, or Unicode escaping
makes every downstream "proved by this evidence" claim unverifiable, so the
serialisation rules are strict and the tests assert byte equality rather than
semantic equality.

Canonical form:

* UTF-8, emitted as real characters rather than ``\\uXXXX`` escapes
* object keys sorted by Unicode code point
* no insignificant whitespace — separators are ``(",", ":")``
* integral floats collapse to integers, so ``1.0`` and ``1`` hash identically
* ``NaN``, ``Infinity``, and ``-Infinity`` are rejected: they are not valid JSON
  and every parser disagrees about them
* no trailing newline

Two hashes are produced, and the distinction is load-bearing (ADR-0002):

``payload_hash``
    Over the payload alone. **Globally stable** — the same payload hashes
    identically on every machine, which is what makes cross-machine duplicate
    detection possible.

``event_hash``
    Over the event envelope, including ``payload_hash`` and the predecessor's
    ``event_hash``. **Locally chained** — tamper-evident within one database, and
    deliberately not a global ledger.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

#: Prefix on every stored digest, so the algorithm is visible in the data and a
#: future migration to another hash does not need to guess what old rows used.
HASH_PREFIX = "sha256:"

#: Fields of the event envelope that participate in the chain hash, in a fixed
#: order. Adding a field here changes every subsequent chain hash, so it is a
#: schema-version change (ADR-0017).
ENVELOPE_HASH_FIELDS = (
    "event_id",
    "schema_version",
    "event_type",
    "recorded_at",
    "occurred_at",
    "project_id",
    "repository_id",
    "run_id",
    "task_id",
    "attempt_id",
    "agent_profile",
    "adapter",
    "model",
    "effort",
    "branch",
    "worktree",
    "base_commit",
    "commit_sha",
    "causal_parent_event_id",
    "source",
    "payload_hash",
    "prev_event_hash",
)


class CanonicalizationError(ValueError):
    """A value cannot be represented in canonical JSON."""


def _normalize(value: Any) -> Any:
    """Recursively coerce a value into canonically-serialisable form."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        # Must precede the int branch: bool is a subclass of int.
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            msg = f"non-finite numbers cannot be canonicalized: {value!r}"
            raise CanonicalizationError(msg)
        if value.is_integer():
            # 1.0 and 1 must hash identically; JSON has one number type and
            # callers should not be able to change a hash by adding a decimal.
            return int(value)
        return value
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                msg = f"object keys must be strings, got {type(key).__name__}"
                raise CanonicalizationError(msg)
            out[key] = _normalize(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    msg = f"type is not canonically serializable: {type(value).__name__}"
    raise CanonicalizationError(msg)


def canonical_json(value: Any) -> str:
    """Serialise ``value`` to canonical JSON."""
    return json.dumps(
        _normalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_bytes(value: Any) -> bytes:
    """Canonical JSON as UTF-8 bytes — what actually gets hashed."""
    return canonical_json(value).encode("utf-8")


def hash_value(value: Any) -> str:
    """SHA-256 over the canonical form of ``value``, prefixed with the algorithm."""
    return HASH_PREFIX + hashlib.sha256(canonical_bytes(value)).hexdigest()


def hash_payload(payload: dict[str, Any]) -> str:
    """Globally stable hash of an event payload."""
    return hash_value(payload)


def hash_envelope(envelope: dict[str, Any], *, payload_hash: str, prev_event_hash: str) -> str:
    """Chain hash over an event envelope.

    Absent optional fields are hashed as ``None`` rather than omitted, so a
    record that never had a ``branch`` and one whose ``branch`` was explicitly
    null hash identically. Omission-versus-null ambiguity is a place where two
    implementations of the same spec drift apart.
    """
    material = {
        field: envelope.get(field) for field in ENVELOPE_HASH_FIELDS if field != "payload_hash"
    }
    material["payload_hash"] = payload_hash
    material["prev_event_hash"] = prev_event_hash
    return hash_value(material)


def hash_text(text: str) -> str:
    """SHA-256 over a plain string.

    Used for memory ``content_hash`` and for failure-signature computation, where
    the input is already a normalised string rather than a structure.
    """
    return HASH_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_content(content: dict[str, Any], text: str) -> str:
    """Content hash of a memory record.

    Covers both the structured ``content`` and the rendered ``text``, because the
    two are stored separately (ADR-0004) and a projection that changed only the
    rendering while leaving the structure alone would otherwise appear unchanged
    — which would make ``rebuild`` determinism tests pass while the digest output
    silently differed.
    """
    return hash_value({"content": content, "text": text})


def verify(value: Any, expected: str) -> bool:
    """Whether ``value`` hashes to ``expected``."""
    return hash_value(value) == expected


def short(digest: str, length: int = 12) -> str:
    """Abbreviate a digest for display. Never used for comparison."""
    body = digest.removeprefix(HASH_PREFIX)
    return body[:length]
