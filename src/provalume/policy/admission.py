"""Boundary 1: everything crossing into the journal passes through here.

The order is fixed and is a security property, not a style choice (threat T11)::

    validate -> size caps -> REDACT -> poisoning scan -> hash -> write

Redaction before hashing means stored hashes cover redacted content. A post-hoc
redaction pass would mean the secret was on disk first, and the hash would attest
to the unredacted version.

Nothing may construct a durable event while skipping this module. That is why
:meth:`Event.create` deliberately does *not* redact or hash: the only path to a
persisted event runs through :func:`admit_event`.
"""

from __future__ import annotations

from typing import Any

from provalume import redact
from provalume.errors import OversizedInputError, ValidationError
from provalume.policy import poisoning
from provalume.schemas.events import (
    MAX_IDENTIFIER_CHARS,
    MAX_PAYLOAD_BYTES,
    MAX_TEXT_FIELD_CHARS,
    Event,
)
from provalume.schemas.trust import Source, TrustState, within_ceiling


class AdmissionResult:
    """The outcome of admitting one event."""

    __slots__ = ("assessment", "event", "landing_state", "reasons")

    def __init__(
        self,
        *,
        event: Event,
        assessment: poisoning.PoisoningAssessment,
        landing_state: TrustState,
        reasons: tuple[str, ...],
    ) -> None:
        self.event = event
        self.assessment = assessment
        self.landing_state = landing_state
        self.reasons = reasons

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<AdmissionResult {self.event.event_id} -> {self.landing_state} "
            f"risk={self.assessment.risk}>"
        )


def _payload_size(payload: dict[str, Any]) -> int:
    from provalume.interchange.hashing import canonical_bytes

    return len(canonical_bytes(payload))


def check_size(payload: dict[str, Any]) -> None:
    """Enforce record and field caps before anything is written (threat T25).

    Rejection is explicit rather than silent truncation: a truncated failure
    excerpt could remove the very line that identifies the failure, producing a
    memory that looks complete and is not.
    """
    size = _payload_size(payload)
    if size > MAX_PAYLOAD_BYTES:
        msg = (
            f"payload is {size} bytes, over the {MAX_PAYLOAD_BYTES}-byte cap. "
            "Trim the excerpt rather than storing the whole log; Provalume stores "
            "evidence, not output."
        )
        raise OversizedInputError(msg)

    def walk(value: Any, path: str) -> None:
        if isinstance(value, str):
            if len(value) > MAX_TEXT_FIELD_CHARS:
                msg = (
                    f"field {path!r} is {len(value)} characters, over the "
                    f"{MAX_TEXT_FIELD_CHARS}-character cap"
                )
                raise OversizedInputError(msg)
        elif isinstance(value, dict):
            for key, item in value.items():
                if isinstance(key, str) and len(key) > MAX_IDENTIFIER_CHARS:
                    msg = f"key {key[:64]!r}... exceeds {MAX_IDENTIFIER_CHARS} characters"
                    raise OversizedInputError(msg)
                walk(item, f"{path}.{key}" if path else str(key))
        elif isinstance(value, (list, tuple)):
            if len(value) > 10_000:
                msg = f"array at {path!r} has {len(value)} items, over the 10000-item cap"
                raise OversizedInputError(msg)
            for i, item in enumerate(value):
                walk(item, f"{path}[{i}]")

    walk(payload, "")


def validate(event: Event) -> None:
    """Structural checks beyond what the pydantic model already enforces."""
    if not event.project_id.strip():
        msg = "project_id is required and cannot be blank (threat T9)"
        raise ValidationError(msg)

    from provalume.interchange.hashing import CanonicalizationError, canonical_json

    try:
        canonical_json(event.payload)
    except CanonicalizationError as exc:
        msg = f"payload is not canonically serializable: {exc}"
        raise ValidationError(msg) from exc


def landing_state(
    event: Event, assessment: poisoning.PoisoningAssessment, *, threshold: float
) -> tuple[TrustState, tuple[str, ...]]:
    """Decide the trust state a memory derived from this event starts in.

    Two independent constraints, and the stricter wins:

    * the source ceiling — an agent cannot produce anything above ``observed``,
      no matter what its payload says (threat T2);
    * the poisoning threshold — anything at or above it lands ``quarantined``
      regardless of source.
    """
    reasons: list[str] = []

    if event.source is Source.HUMAN:
        state = TrustState.OBSERVED
        reasons.append("human-sourced")
    elif event.source in {Source.KERNEL, Source.ADAPTER}:
        state = TrustState.OBSERVED
        reasons.append(f"{event.source}-sourced structured report")
    else:
        state = TrustState.QUARANTINED
        reasons.append(f"{event.source}-sourced content is untrusted by construction")

    if poisoning.exceeds_threshold(assessment, threshold):
        if state is not TrustState.QUARANTINED:
            reasons.append(
                f"forced to quarantined: poisoning risk {assessment.risk:.2f} "
                f"at or above threshold {threshold:.2f}"
            )
        else:
            reasons.append(f"poisoning risk {assessment.risk:.2f} at or above threshold")
        state = TrustState.QUARANTINED
    elif assessment.flagged:
        reasons.append(
            f"poisoning risk {assessment.risk:.2f} below threshold; ranking penalty applies"
        )

    if not within_ceiling(state, event.source):  # pragma: no cover - defensive
        msg = f"source {event.source} may not produce trust state {state}"
        raise ValidationError(msg)

    return state, tuple(reasons)


def admit_event(
    event: Event,
    *,
    poisoning_threshold: float = 0.5,
) -> AdmissionResult:
    """Run the full admission pipeline and return an event that is safe to store.

    The returned event has been redacted and carries its redaction metadata.
    Hashing happens in the journal, over exactly this content.
    """
    validate(event)
    check_size(event.payload)

    redacted_payload, report = redact.redact_structured(event.payload)

    # Scan *after* redaction: a credential replaced by [REDACTED] should not also
    # be scored as a poisoning signal, and scanning post-redaction text is what a
    # future reader will actually see.
    assessment = poisoning.assess_structured(redacted_payload)

    state, reasons = landing_state(event, assessment, threshold=poisoning_threshold)

    integrity = dict(event.integrity)
    if assessment.flagged:
        integrity["poisoning"] = {
            "risk": assessment.risk,
            "matches": list(assessment.matches),
            "families": list(assessment.families),
        }

    admitted = event.model_copy(
        update={
            "payload": redacted_payload,
            "redaction": report.to_dict(),
            "integrity": integrity,
        }
    )
    return AdmissionResult(
        event=admitted,
        assessment=assessment,
        landing_state=state,
        reasons=reasons,
    )


def admit_text(text: str, *, field: str = "text") -> tuple[str, dict[str, Any]]:
    """Admit a free-text field: cap, redact, and report.

    Used for memory ``text`` where the value did not arrive inside an event
    payload — a proposal's rendered text, for instance.
    """
    if len(text) > MAX_TEXT_FIELD_CHARS:
        msg = f"{field} is {len(text)} characters, over the {MAX_TEXT_FIELD_CHARS} cap"
        raise OversizedInputError(msg)
    redacted, report = redact.redact_text(text)
    return redacted, report.to_dict()
