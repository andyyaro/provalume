"""The typed MCP tools must return records of the type they name.

`memory_types` is a ranking *nudge* inside the retrieval engine, not a filter: a
mismatched type costs a result about 0.10 of score, which a strong lexical match
of another type easily outranks. `_typed_query` then filtered the engine's
already-truncated result list, so `query_failures` reported "no matching records"
to a client while several matching gotchas sat in the store — the worst possible
answer, since the client asked precisely so it would not repeat a known failure.
"""

from __future__ import annotations

import json

from provalume.mcp.server import McpServer
from provalume.schemas.memories import MemoryFilter, MemoryType
from provalume.sdk.client import Provalume


def stocked(pv: Provalume) -> Provalume:
    """Six procedures that match the query exactly, three gotchas that match it
    only in part — the ordinary shape of a repository with a healthy deploy path
    and a few failures behind it."""
    for index in range(6):
        pv.record_verification(
            command=f"deploy staging service-{index} --wait",
            passed=True,
            purpose="deploy staging",
        )
    for index in range(3):
        pv.record_verification(
            command=f"deploy worker-{index}",
            passed=False,
            excerpt="ConnectionRefused: worker socket",
            error_kind="deploy_error",
        )
    return pv


def query(server: McpServer, tool: str, **arguments: object) -> dict:
    response = server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            }
        )
    )
    assert response is not None
    return dict(response["result"]["structuredContent"])


def test_query_failures_finds_gotchas_outranked_by_another_type(pv: Provalume) -> None:
    stocked(pv)
    stored = pv.memory_records(MemoryFilter(project_id=pv.project_id, limit=100))
    assert sum(1 for m in stored if m.memory_type is MemoryType.GOTCHA) == 3

    payload = query(McpServer(pv), "query_failures", query="deploy staging", limit=5)

    assert payload["count"] == 3, "prior failures exist and the tool reported none"
    assert {r["memory_type"] for r in payload["results"]} == {"gotcha"}


def test_a_typed_query_still_honours_the_caller_s_limit(pv: Provalume) -> None:
    for index in range(8):
        pv.record_verification(
            command=f"migrate shard-{index}",
            passed=False,
            excerpt="LockTimeout: shard busy",
            error_kind=f"lock_timeout_{index}",
        )

    payload = query(McpServer(pv), "query_failures", query="migrate shard", limit=2)

    assert payload["count"] == 2


def test_query_procedures_does_not_leak_another_type(pv: Provalume) -> None:
    stocked(pv)

    payload = query(McpServer(pv), "query_procedures", query="deploy", limit=10)

    assert payload["results"]
    assert {r["memory_type"] for r in payload["results"]} == {"procedural"}
