"""The MCP permission model (ADR-0012).

The design constraint, stated once: **an MCP client is driven by a model that
reads attacker-controlled repository content.** Whatever it can call, an attacker
can eventually reach.

So the dangerous operations are not gated — they are **absent**. There is no
promote tool, no invalidate tool, no supersede tool, no delete tool, no
scope-movement tool, no rebuild, no import, no path parameter of any kind. A
disabled tool is one misconfiguration away from enabled; a tool that does not
exist cannot be enabled by a config file.

``tests/security/test_mcp_surface.py`` asserts the exact tool-name set, so adding
one of the forbidden operations fails CI rather than shipping quietly.

The hands-on trial that motivated this is recorded in
``docs/research/COMPETITOR_TRIALS.md`` §2.2: a widely-used MCP memory server
exposes an unrestricted write *and* a governance-delete to any connected client.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Final

#: Read tools. Always available, in every profile.
READ_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "recall",
        "explain",
        "query_failures",
        "query_decisions",
        "query_procedures",
        "query_facts",
        "query_provenance",
        "preflight",
    }
)

#: Write tools. Every one lands its record at `quarantined`; none grants trust.
WRITE_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "propose",
        "record_observation",
        "report_failure",
        "report_outcome",
    }
)

#: Operations that must never appear on the MCP surface. This set exists so the
#: prohibition is executable rather than a comment: the server asserts its own
#: tool list against it at construction, and a security test asserts it too.
FORBIDDEN_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "promote",
        "demote",
        "invalidate",
        "supersede",
        "reject",
        "delete",
        "forget",
        "purge",
        "widen_scope",
        "move_scope",
        "set_trust",
        "rebuild",
        "import",
        "import_records",
        "export",
        "audit",
        "open_database",
        "set_database",
        "configure",
        # Freshness (ADR-0020, threat T27): the axis is machine-observed and
        # operator-gated. An MCP client that could trigger, relabel, or re-run
        # would hold exactly the unattended-execution lever T27 exists to deny.
        "reverify",
        "reverify_record",
        "trigger_freshness",
        "set_freshness",
        "mark_stale",
        "mark_current",
        "record_blast_radius",
    }
)

#: Bounds (threat T24). Exceeding one returns a tool execution error with an
#: actionable reason, not a transport failure, so a model can correct itself.
DEFAULT_RATE_LIMIT_PER_MINUTE: Final = 60
DEFAULT_TIMEOUT_S: Final = 10.0
MAX_RESPONSE_BYTES: Final = 64 * 1024
MAX_RESULTS: Final = 50
MAX_INPUT_FIELD_CHARS: Final = 8 * 1024


@dataclass(frozen=True)
class PermissionProfile:
    """What a connected MCP client may do.

    Chosen by the operator at launch. **A client cannot change its own profile** —
    there is no tool to do so, which is why this is a frozen dataclass built
    before the server starts rather than state the protocol can touch.
    """

    name: str
    allow_writes: bool
    max_results: int = MAX_RESULTS
    max_response_bytes: int = MAX_RESPONSE_BYTES
    max_input_field_chars: int = MAX_INPUT_FIELD_CHARS
    timeout_s: float = DEFAULT_TIMEOUT_S

    @classmethod
    def default(cls) -> PermissionProfile:
        """Read tools plus quarantined writes. For single-operator local use."""
        return cls(name="default", allow_writes=True)

    @classmethod
    def read_only(cls) -> PermissionProfile:
        """Read tools only. Recommended for shared or untrusted environments."""
        return cls(name="read-only", allow_writes=False)

    def tool_names(self) -> frozenset[str]:
        return READ_TOOLS | WRITE_TOOLS if self.allow_writes else READ_TOOLS

    def allows(self, tool: str) -> bool:
        return tool in self.tool_names()

    def refusal_reason(self, tool: str) -> str:
        """Why a tool call was refused, phrased so a model can act on it."""
        if tool in FORBIDDEN_TOOLS:
            return (
                f"'{tool}' is not available over MCP and never will be. Promotion, "
                "invalidation, supersession, and maintenance are operator actions "
                "performed through the provalume CLI, because an MCP client cannot "
                "be trusted to grant trust. Propose a memory instead; it will be "
                "stored as quarantined and can be promoted later if deterministic "
                "evidence supports it."
            )
        if tool in WRITE_TOOLS and not self.allow_writes:
            return (
                f"'{tool}' is a write tool and this server is running in read-only "
                "mode. Read tools remain available."
            )
        return f"unknown tool '{tool}'"


class RateLimiter:
    """Token bucket over a sliding window.

    Bounds write volume from an untrusted client. Sliding-window rather than a
    fixed bucket so a client cannot save up a minute of quiet and then burst.
    """

    def __init__(self, *, per_minute: int = DEFAULT_RATE_LIMIT_PER_MINUTE) -> None:
        self.per_minute = max(1, per_minute)
        self._calls: list[float] = []

    def check(self, *, now: float | None = None) -> bool:
        """Record a call and report whether it is within the limit."""
        current = time.monotonic() if now is None else now
        cutoff = current - 60.0
        self._calls = [t for t in self._calls if t > cutoff]
        if len(self._calls) >= self.per_minute:
            return False
        self._calls.append(current)
        return True

    def remaining(self, *, now: float | None = None) -> int:
        current = time.monotonic() if now is None else now
        cutoff = current - 60.0
        active = [t for t in self._calls if t > cutoff]
        return max(0, self.per_minute - len(active))


@dataclass
class CallAudit:
    """One recorded MCP call.

    Refusals are recorded as well as successes. A refused call is a security
    signal, and dropping it silently is what an attacker wants.
    """

    tool: str
    allowed: bool
    reason: str = ""
    client: str = ""
    arguments_summary: str = ""
    elapsed_ms: float = 0.0


@dataclass
class AuditLog:
    """In-memory audit of this session's calls.

    Durable auditing goes to the journal as `mcp.call` / `mcp.refused` events;
    this is the live view the server uses for its own bookkeeping.
    """

    entries: list[CallAudit] = field(default_factory=list)
    max_entries: int = 1000

    def record(self, entry: CallAudit) -> None:
        self.entries.append(entry)
        if len(self.entries) > self.max_entries:
            del self.entries[: len(self.entries) - self.max_entries]

    @property
    def refusals(self) -> list[CallAudit]:
        return [e for e in self.entries if not e.allowed]


def assert_surface_is_safe(tool_names: frozenset[str]) -> None:
    """Fail loudly if a forbidden operation ever appears in the tool list.

    Called by the server at construction, so the invariant holds at runtime and
    not only in a test file someone might forget to run.
    """
    breach = tool_names & FORBIDDEN_TOOLS
    if breach:
        msg = (
            f"MCP tool surface includes forbidden operation(s): {', '.join(sorted(breach))}. "
            "Promotion, invalidation, supersession, and maintenance must never be "
            "reachable from an MCP client (ADR-0012)."
        )
        raise AssertionError(msg)


def truncate(text: str, limit: int) -> str:
    """Bound a response, saying so rather than cutting silently."""
    if len(text) <= limit:
        return text
    notice = f"\n\n[truncated: response exceeded {limit} characters]"
    return text[: max(0, limit - len(notice))] + notice
