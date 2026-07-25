"""Exception hierarchy.

Every error Provalume raises deliberately derives from :class:`ProvalumeError`, so
an embedding application can catch one type. Integrations are expected to catch
this and degrade rather than fail a run — see ADR-0014 for which failures should
fail open and which must fail closed.
"""

from __future__ import annotations


class ProvalumeError(Exception):
    """Base class for every error raised by Provalume."""


class ConfigError(ProvalumeError):
    """Configuration is missing, malformed, or contradictory."""


class StoreError(ProvalumeError):
    """The database could not be opened, migrated, or written."""


class SchemaVersionError(StoreError):
    """The database schema is newer than this version of Provalume supports.

    Refusing to open is deliberate (ADR-0003): operating on a schema whose
    semantics are unknown corrupts data quietly.
    """


class IntegrityError(StoreError):
    """An integrity check failed: hash chain, projection consistency, or pragmas."""


class AppendOnlyViolation(StoreError):
    """An attempt was made to modify or delete a journal event."""


class ValidationError(ProvalumeError):
    """Input failed validation, a size cap, or a schema check."""


class OversizedInputError(ValidationError):
    """A field or record exceeded its configured size cap (threat T25)."""


class PolicyViolation(ProvalumeError):
    """A lifecycle transition was refused by policy.

    Carries the named rule that refused it so the refusal is as auditable as an
    approval would have been. Refusals are recorded, not swallowed (ADR-0005).
    """

    def __init__(self, message: str, *, rule: str = "", memory_id: str = "") -> None:
        super().__init__(message)
        self.rule = rule
        self.memory_id = memory_id


class TrustError(PolicyViolation):
    """A promotion was refused because the required evidence was absent."""


class ScopeError(PolicyViolation):
    """A scope boundary would have been crossed without the required authority."""


class PathConfinementError(ProvalumeError):
    """A path resolved outside its permitted root (threat T21)."""


class InterchangeError(ProvalumeError):
    """A JSONL record could not be exported or imported."""


class UnknownRecordVersion(InterchangeError):
    """A JSONL record declares a version this build cannot interpret.

    Never partially interpreted: a record from the future is a record that cannot
    be validated (ADR-0017).
    """


class SignatureError(InterchangeError):
    """A signature was absent, malformed, or could not be verified.

    Always fail-closed. Includes the case where the optional ``signatures`` extra
    is not installed, so an Ed25519-signed record is quarantined rather than
    accepted unverified (threat T18).
    """


class RetrievalError(ProvalumeError):
    """A query could not be executed."""


class BudgetExceeded(ProvalumeError):
    """A digest could not be composed within the requested budget.

    Raised only when the budget is too small to hold even the mandatory banner;
    ordinary overflow is handled by omitting items and reporting the count.
    """


class EmbedderUnavailable(ProvalumeError):
    """Vector retrieval was requested but no embedder backend is usable.

    Callers should fall back to lexical retrieval, which is the tested default
    path (ADR-0013).
    """


class McpProtocolError(ProvalumeError):
    """An MCP request violated the protocol."""


class RateLimited(ProvalumeError):
    """An MCP client exceeded its rate limit (threat T24)."""
