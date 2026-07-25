"""The MCP transport's bounds, on the paths an untrusted client controls.

Two of them were reachable from a client that ignores every rule the server
states. A refused tool call was journalled *before* the rate limiter was
consulted, so `promote` in a loop wrote an unbounded number of durable
`mcp.refused` events while the limit never moved — the one call shape an
attacker gets for free was the one shape outside the bound (threat T24). And a
deeply-nested JSON line raised `RecursionError` out of `json.loads`, which
`handle_line` did not catch, ending the session on one bad line instead of
returning a parse error.
"""

from __future__ import annotations

import json
from io import StringIO

from provalume.mcp.server import PARSE_ERROR, McpServer
from provalume.schemas.events import EventFilter
from provalume.sdk.client import Provalume


def call(name: str, **arguments: object) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )


def journalled(pv: Provalume) -> int:
    return len(pv.events(EventFilter(project_id=pv.project_id, limit=10_000)))


# --- Rate limiting covers refusals -----------------------------------------


def test_a_refused_call_consumes_rate_limit_budget(pv: Provalume) -> None:
    """Budget is spent on every inbound call, not only the ones that proceed."""
    server = McpServer(pv, rate_limit_per_minute=5)

    server.handle_line(call("promote"))

    assert server.limiter.remaining() == 4


def test_looping_on_a_forbidden_tool_cannot_grow_the_journal(pv: Provalume) -> None:
    """The attack: a free, unbounded durable write from an untrusted client."""
    server = McpServer(pv, rate_limit_per_minute=5)
    for _ in range(5):
        server.handle_line(call("recall", query="x"))
    before = journalled(pv)

    for _ in range(200):
        server.handle_line(call("promote"))

    written = journalled(pv) - before
    assert written <= 2, f"200 refused calls wrote {written} journal events"
    assert len(server.audit.refusals) == 200, "the in-memory audit still sees every one"


def test_a_burst_of_rate_limited_calls_is_summarised_once(pv: Provalume) -> None:
    """Suppression is reported, not silent: a dropped refusal is what an
    attacker wants."""
    server = McpServer(pv, rate_limit_per_minute=1)
    server.handle_line(call("recall", query="x"))
    for _ in range(10):
        server.handle_line(call("recall", query="x"))

    server.limiter._calls.clear()
    server.handle_line(call("recall", query="x"))

    refusals = [
        e
        for e in pv.events(EventFilter(project_id=pv.project_id, limit=10_000))
        if e.event_type.value == "mcp.refused"
    ]
    assert refusals, "the start of the burst is still recorded"
    assert any(e.payload.get("suppressed") == 9 for e in refusals), (
        f"no summary of the suppressed calls: {[e.payload for e in refusals]}"
    )


# --- Parse errors stay parse errors ----------------------------------------


def test_deeply_nested_json_is_a_parse_error_not_a_crash(pv: Provalume) -> None:
    server = McpServer(pv)

    response = server.handle_line("[" * 100_000 + "]" * 100_000)

    assert response is not None
    assert response["error"]["code"] == PARSE_ERROR


def test_one_unparseable_line_does_not_end_the_session(pv: Provalume) -> None:
    """The module docstring's promise: a protocol error is an error response,
    never an opaque transport failure."""
    server = McpServer(pv)
    nested = "[" * 100_000 + "]" * 100_000
    ping = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    stdin = StringIO(f"{nested}\n{ping}\n")
    stdout = StringIO()

    server.serve_stdio(stdin=stdin, stdout=stdout)

    answers = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [a.get("id") for a in answers] == [None, 2], "the well-formed ping went unanswered"


def test_an_oversized_message_is_refused_before_parsing(pv: Provalume) -> None:
    """The per-argument cap only applies once a message is parsed, and parsing is
    itself work an untrusted client can ask for."""
    server = McpServer(pv)

    response = server.handle_line(call("recall", query="x" * (2 * 1024 * 1024)))

    assert response is not None
    assert "result" not in response, "the message was parsed before its size was checked"
    assert response["error"]["code"] == PARSE_ERROR
    assert "over the" in response["error"]["message"]
