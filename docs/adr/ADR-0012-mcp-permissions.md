# ADR-0012: MCP server and permission model

**Status:** Accepted · **Date:** 2026-07-25

## Context

An MCP surface is what makes Provalume usable outside a single orchestrator. It is
also the highest-risk interface in the system, because **an MCP client is driven by a
model that reads attacker-controlled repository content.**

The hands-on trial of `@agentmemory/mcp` made this concrete: its 7-tool surface
includes `memory_save` — an unrestricted write — and `memory_governance_delete` — a
destructive delete — both callable by any connected client with no tier, no review,
and no separate confirmation. That is a memory-poisoning primitive with a delete
button next to it.

Separately, the official `mcp` Python SDK (1.28.1, MIT) pulls `starlette`,
`uvicorn`, `httpx`, `httpx-sse`, `sse-starlette`, `pyjwt[crypto]`, `jsonschema`,
`pydantic-settings`, `python-multipart`, and `anyio`. For a stdio server that is a
large mandatory surface, and it puts HTTP-capable libraries into a project whose
privacy claim is "no network".

## Decision

**Implement MCP stdio JSON-RPC 2.0 directly against the published specification,
using only the standard library. Expose read tools plus `propose`. Expose no
promotion tool at all.**

### No SDK dependency

MCP stdio transport is newline-delimited JSON-RPC 2.0. The parts needed —
`initialize`, `notifications/initialized`, `tools/list`, `tools/call`, `ping` — are
implementable in `json` and `sys.stdin`/`stdout`.

Verified against the specification rather than recalled: the current protocol
revision is **`2025-11-25`**. Provalume advertises it and negotiates down to
`2025-06-18` and `2024-11-05`.

The cost is owning protocol conformance. The benefit is three pure-Python
dependencies total, no HTTP stack, and a testable `no_network` assertion.

### The tool surface

**Read tools** — always available:

| Tool | Returns |
|---|---|
| `recall` | Ranked memories with explanations, scope-filtered, budget-bounded |
| `explain` | Full provenance and score breakdown for one memory |
| `query_failures` | Gotchas matching a subject or signature |
| `query_decisions` | Decision records with rationale and rejected alternatives |
| `query_procedures` | Verified commands and runbooks |
| `query_facts` | Semantic facts, with current-truth labelling |
| `query_provenance` | The evidence chain for one memory |
| `preflight` | The pre-action warning gate |

**Write tools** — enabled by default, gated, all landing at `quarantined`:

| Tool | Effect |
|---|---|
| `propose` | Submit a candidate memory. `source=agent`, `trust_state=quarantined`. |
| `record_observation` | A structured observation |
| `report_failure` | A structured failure with a computed signature |
| `report_outcome` | A structured task outcome |

**Absent — not disabled, not gated, absent:**

`promote` · `invalidate` · `supersede` · `reject` · any scope-movement tool ·
`rebuild` · `import` · `export` · `audit` · anything taking a filesystem path

Absence is the control. A disabled tool is a configuration flag away from enabled; a
tool that does not exist cannot be enabled by a misconfiguration.
`tests/security/test_mcp_surface.py` asserts the tool-name set, so adding one of
these fails CI rather than shipping.

`audit` and `export` are excluded even though they are read-only: an audit report
leaks scope metadata and an export leaks everything. Both stay on the CLI, where the
caller is the operator.

### Permission profiles

| Profile | Read | Write | Use |
|---|---|---|---|
| `read-only` | yes | no | Shared or untrusted environments. Recommended default there. |
| `default` | yes | `propose` and structured reports | Single-operator local use |

Selected at launch by the operator via `provalume serve-mcp --read-only`. **A client
cannot change its own profile** — there is no tool to do so.

### Project scoping

The database path and `project_id` are fixed at launch. **The server accepts no path
parameter from any client, ever** (threat T21). A client cannot open another
project's database, traverse the filesystem, or discover what other projects exist.

### Bounds

| Control | Default | Reason |
|---|---|---|
| Rate limit | 60 calls/min, token bucket | Bounds write volume from an untrusted client |
| Per-request timeout | 10 s | No hung request |
| Max response size | 64 KB | Bounds context consumption |
| Max result count | 50 | Bounds work per call |
| Max input field size | 8 KB | Oversized-input rejection (threat T25) |

Exceeding a bound returns a tool execution error (`isError: true`) with an actionable
reason, per the specification's distinction between protocol errors and tool errors —
so a model can correct itself rather than seeing an opaque transport failure.

### Audit logging

Every call is recorded as an event with `source=agent`, including refusals. A
refused call is a security signal and dropping it silently is what an attacker wants.

### Spec-mandated security requirements

The specification says servers **MUST** validate all tool inputs, implement access
controls, rate-limit invocations, and sanitise outputs. All four are implemented and
are the four bullets above plus schema validation on every argument.

## Consequences

**Good.** Provalume works with any MCP client with no extra install weight. Three
pure-Python dependencies total. The privacy claim ("no network code") stays literally
true and testable. The dangerous operations are not reachable from the dangerous
interface.

**Bad.** Owning protocol conformance. If MCP adds a revision, Provalume must
implement it, whereas an SDK user gets it free. Mitigated by the surface being small
and by version negotiation degrading gracefully. Conformance tests live in
`tests/integration/test_mcp_protocol.py`.

**Bad.** No HTTP transport, no SSE, no OAuth. Stdio only. Fine for local tooling,
which is the target; a hosted deployment would need the SDK, and a hosted deployment
is out of scope ([ADR-0001](ADR-0001-identity-and-scope.md)).

**Also bad.** An agent using Provalume through MCP cannot make anything trusted. Its
proposals sit in `quarantined` until an evidence event arrives from elsewhere. This
will feel broken to someone expecting a memory tool to remember what they tell it.
It is the design, and it is what the tool descriptions say.

## Alternatives rejected

**Use the official SDK.** Ten transitive dependencies including an HTTP stack and a
JWT library, for a stdio server. Would also make "no network code" unverifiable.

**Expose promotion behind a permission flag.** One misconfiguration from a
poisoning primitive. The trial of `@agentmemory/mcp` is what this refusal is
reacting to.

**Expose `audit` as a read tool.** Leaks scope metadata to an untrusted client.

**Accept a database path parameter.** Path traversal, directly.

**Trust `clientInfo`.** Client-supplied and unauthenticated. Recorded for the audit
log; never used for authorisation.
