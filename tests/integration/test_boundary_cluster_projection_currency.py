"""Events nothing projects must still leave the projections looking current.

`preflight(record=True)` and the MCP audit trail wrote their events without
projecting them, which advances the journal's sequence but not the projection
watermark. `provalume audit` compares the two, so after any warning that matched
it reported "projections are behind the journal" and advised a rebuild that fixes
nothing. In an orchestrator run that fires constantly, which devalues the one
signal that would surface a genuinely stale projection.
"""

from __future__ import annotations

import json

from provalume.mcp.server import McpServer
from provalume.sdk.client import Provalume


def currency_findings(pv: Provalume) -> list[str]:
    return [
        f.detail
        for f in pv.audit().findings
        if f.check == "projection_currency" and f.severity != "info"
    ]


def seeded(pv: Provalume) -> Provalume:
    pv.record_verification(
        command="pytest -q",
        passed=False,
        excerpt="ImportError: no module named provalume",
        error_kind="import_error",
    )
    return pv


def test_a_recorded_warning_keeps_the_projections_current(pv: Provalume) -> None:
    seeded(pv)

    result = pv.preflight(command="pytest -q", error_kind="import_error")

    assert result.matched, "the fixture must actually trip the gate"
    assert pv.memories.projection_seq() == pv.journal.latest_seq()
    assert not currency_findings(pv)


def test_mcp_call_auditing_keeps_the_projections_current(pv: Provalume) -> None:
    seeded(pv)
    server = McpServer(pv)

    server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "recall", "arguments": {"query": "pytest"}},
            }
        )
    )

    assert pv.memories.projection_seq() == pv.journal.latest_seq()
    assert not currency_findings(pv)


def test_an_unprojected_event_still_reports_a_stale_projection(pv: Provalume) -> None:
    """The signal has to survive: an event that genuinely was not projected must
    still be reported, or the fix would have silenced the check instead."""
    pv.record_event(
        "verification.passed",
        payload={"command": "cargo test", "exit_code": 0},
        project=False,
    )

    assert currency_findings(pv)
