# Research validation

**Performed:** 2026-07-25

[`MEMORY_SYSTEM_RESEARCH.md`](MEMORY_SYSTEM_RESEARCH.md) is preserved in this
repository unchanged (SHA-256
`4497e0ed49b004f4b151c7e74527c8f892df753a936c0fbe765442ff9d19a549`), including its
original date and its own closing caveat that it was desk research and that no
candidate had been installed.

This document records what happened when its load-bearing claims were checked
against primary sources and actual code. Competitor and licensing corrections live
in [`COMPETITOR_TRIALS.md`](COMPETITOR_TRIALS.md); this file covers everything
else, plus the one place where Provalume deliberately does not follow the report's
recommendation.

---

## 1. The deliberate deviation: standalone first, not Orkestra-first

**The report recommends** (§1, §7, §9): build the memory layer *inside* Orkestra
first, dogfood it for ≥1 month, then extract it to its own repository following
the Graphiti/Beads pattern. Its stated spin-out trigger is (a) ≥1 month of real
dogfooding, (b) a one-sentence identity, (c) an MCP surface working from a
non-Orkestra client.

**Provalume does the opposite: it ships standalone from day one,** with Orkestra
as one integration among several rather than as its host. This is a directed
decision by the project owner, not an oversight, and it is recorded as such in
[ADR-0001](../adr/ADR-0001-identity-and-scope.md) and
[ADR-0014](../adr/ADR-0014-orkestra-integration-boundary.md).

The honest accounting of that choice:

**What is lost.** The report's strongest argument is real — a month of dogfooding
inside a working orchestrator would rank which memory failures actually recur,
turning the schema from reasoned design into evidence-backed design. Provalume
ships without that data. Its schema is derived from the literature, from the
competitor review, and from Orkestra's existing event/attempt/ledger shape, but
not from measured failure frequencies in production runs. **This is the single
largest known weakness of v0.1.0** and it is stated in
[`docs/reference/LIMITATIONS.md`](../reference/LIMITATIONS.md) rather than buried.

**What is gained, and why the trade is defensible.**

1. **The dependency direction comes out right by construction.** Building inside
   Orkestra and extracting later is exactly how a memory layer accretes hidden
   couplings to its host's types, its scheduler, and its config. Building
   standalone forces every integration point through a documented adapter
   boundary. Provalume's core does not import Orkestra; the Orkestra adapter
   imports Provalume; and Provalume is optional to Orkestra at runtime.
2. **The replayable eval harness substitutes for some of the missing dogfood
   data.** Twenty scenarios encode the failure modes the report and the literature
   name — repeated failed fixes, environment gotchas, stale facts, rejected-branch
   knowledge, cross-scope leakage, poisoning. They are synthetic, which is weaker
   than production traces, and they are *reproducible*, which production traces
   were never going to be.
3. **The threat model is written before the engine, not retrofitted.** The report's
   own §8.1 calls memory poisoning "your differentiator's Achilles heel" and says
   to write the threat model before writing code. Extracting from a host later
   makes that harder, not easier, because the trust boundary is already blurred by
   the host's internals.

**How the gap gets closed.** Dogfooding is not skipped, it is sequenced after
v0.1.0: the Orkestra integration lands as a *draft* pull request, and the
requirements-mining pass the report calls for (§8.5 — query real `events`/`ledger`
data to rank recurring failures) is tracked as post-v0.1.0 work in
[`ROADMAP.md`](../../ROADMAP.md). Schema changes that mining produces are what the
compatibility and migration machinery ([ADR-0017](../adr/ADR-0017-compatibility-and-versioning.md))
exists to absorb.

---

## 2. Orkestra codebase claims — verified against source

Read-only inspection of the local checkout at `~/Downloads/Orkestra`
(branch `nested-repo-fix-v0.4.4`, HEAD `1cdbc9ad`, working tree dirty — another
session holds write access; nothing was modified). The report's file paths were
stated as correct at v0.4.2; the checkout is at v0.4.4.

| Report claim | Verified? | Detail found |
|---|---|---|
| One prompt choke point at `Orchestrator._render_brief()` in `src/orkestra/kernel/scheduler.py` | **Yes** | Defined at `scheduler.py:757`; called at `scheduler.py:691` and its return value becomes `TaskBrief.instructions`, which every adapter receives. A digest spliced there reaches all adapters with no per-adapter code. |
| `WorkspaceManager.commit_workspace()` runs `git add -A` over the whole worktree, so an injected memory file would be swept into the agent's commit | **Yes** | `worktrees.py:100` → `GitRepo.add_all_and_commit(message)`. The trap is real and confirmed. Provalume's materialization therefore removes generated files deterministically before staging rather than relying on `.gitignore` ([ADR-0015](../adr/ADR-0015-worktree-materialization.md)). |
| Rich existing signal in `events`, `attempts`, `ledger`, `observations`, `decisions`, `usage_log` | **Yes** | `store/migrations.py` creates exactly: `runs`, `tasks`, `task_deps`, `attempts`, `events`, `decisions`, `observations`, `ledger`, `workspaces`, `usage_log`. |
| Review verdicts live only inside `attempts.result` JSON; a dedicated capture is needed to learn from rejections | **Yes** | No `reviews` table exists. Confirms Provalume must accept review verdicts as explicit structured events rather than expecting to read them from a table. |
| Constraints: Python ≥3.12, mypy strict, Apache-2.0, stdlib `sqlite3` WAL, no ORM, four dependencies (pydantic/typer/rich/tomlkit) | **Yes** | `pyproject.toml` confirms all of it: `requires-python = ">=3.12"`, `license = "Apache-2.0"`, `[tool.mypy] strict = true`, deps exactly `pydantic>=2.9,<3`, `typer>=0.15,<1`, `rich>=13.9`, `tomlkit>=0.13`. `store/db.py` sets `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000` and runs a linear `MIGRATIONS` list with a `schema_version` table, rejecting databases newer than the code supports. |
| Redaction at write via `orkestra.redact` | **Yes** | `redact.py` exists with an ordered rule table covering Anthropic/OpenAI `sk-`, GitHub, GitLab, Google, GCP, AWS, Azure, Slack, npm, PyPI tokens, JWTs, bearer headers, URL userinfo, PEM blocks, and generic credential assignments. Provalume implements its own equivalent rather than importing it, because the core must not import Orkestra. |
| Distribution name | **Correction** | The report never states it; for the record the PyPI distribution is `orkestra-runtime` (version 0.4.4), while the import package and CLI are `orkestra`. The integration guide uses the correct name. |
| Scoping: nothing defines a `~/.orkestra/`; global cross-project memory is net-new | **Yes** | Consistent with what is in tree. Provalume defers global scope entirely ([ADR-0016](../adr/ADR-0016-global-memory-deferral.md)). |

### Not verified, and therefore not built on

The report's §8.6 flags two facts as resting on secondary sources: Antigravity's
native `AGENTS.md` support, and the effect of the Claude adapter's
`--setting-sources user` flag on `CLAUDE.md` auto-loading inside worktrees. It
recommends testing both before building on them.

**Neither was tested, and Provalume v0.1.0 does not depend on either.** Testing
them requires invoking real vendor CLIs against a real worktree, which would mean
either mutating the active Orkestra checkout (prohibited for this session) or
consuming a vendor quota for a result that only gates an optional feature.
Provalume's primary injection path is the **prompt splice** through
`_render_brief()`, which needs neither fact. Vendor context-file materialization
is implemented as an *opt-in* capability with the cleanup guarantee that the
verified `git add -A` behaviour requires, and its documentation states plainly
that per-vendor auto-loading behaviour is unverified. See
[ADR-0015](../adr/ADR-0015-worktree-materialization.md) and
[`docs/integration/ORKESTRA.md`](../integration/ORKESTRA.md).

---

## 3. Protocol and standards claims

| Claim | Status |
|---|---|
| MCP protocol revision | **Corrected.** The current revision is **`2025-11-25`** (per the MCP specification's own versioning page). Provalume advertises `2025-11-25` and negotiates down to `2025-06-18` and `2024-11-05`. The `initialize` / `notifications/initialized` lifecycle, `tools/list` pagination, and `tools/call` result shape (`content`, `structuredContent`, `isError`) were read from the published specification rather than recalled. |
| Tool-result error semantics | Verified from spec: protocol errors use JSON-RPC `error`; tool execution errors use a normal result with `isError: true`. Provalume follows this distinction — an unknown tool is `-32601`/`-32602`, a rejected write is `isError: true` with an actionable reason. |
| Spec's own security guidance | Verified and adopted: servers **MUST** validate all tool inputs, implement access controls, rate-limit invocations, and sanitise outputs. Provalume's MCP layer does all four; see [ADR-0012](../adr/ADR-0012-mcp-permissions.md). |

## 4. Benchmark claims

The report's benchmark position was accepted, not re-derived, and Provalume's
practice follows from it rather than from any re-verification:

- **LoCoMo is not used**, as a headline or otherwise.
- **No LongMemEval-V2 score is claimed.** The report is right that it is the
  relevant benchmark; it is a conversational-trajectory benchmark with its own
  harness, and Provalume v0.1.0 does not run it. What ships is a
  LongMemEval-V2-*style* replayable harness over software-agent task
  trajectories — twenty scenarios, own fixtures, own metrics, committed and
  re-runnable. Its methodology and its limits are documented in
  [`docs/reference/BENCHMARKS.md`](../reference/BENCHMARKS.md).
- **No comparative superiority claim is published anywhere in this repository.**
  The lexical-vs-hybrid comparison in the harness compares Provalume against
  Provalume, on Provalume's own fixtures, and says so.

Vendor-reported numbers from any project in this space are treated as
marketing-adjacent, per the report's own closing caveat.

## 5. Standing corrections summary

| # | Corrected claim | Correction |
|---|---|---|
| 1 | Build inside Orkestra first, extract later | Directed deviation: standalone from day one. §1 above. |
| 2 | MCP revision unstated / `2025-06-18` assumed | Current revision is `2025-11-25`. |
| 3 | Beads ~18.7k stars | 25,639. |
| 4 | `madebyaris/agent-orchestration` a notable near-competitor | 14 stars; a personal project. |
| 5 | `agentmemory` as one project | Two unrelated projects share the name across npm and PyPI. |
| 6 | ProjectMem's prominence implied | 202 stars. The idea is excellent and effectively un-owned. |
| 7 | mem0 as Python-centric | Primary language is now TypeScript. |
| 8 | Orkestra distribution name unstated | `orkestra-runtime`, currently 0.4.4. |
| 9 | Official MCP Python SDK as the obvious way to serve MCP | Rejected on footprint; stdlib implementation instead. |
| 10 | claude-mem footprint unquantified | 41 MB / 24 packages, requires Bun for runtime, optional Docker+Postgres+Redis tier, telemetry on by default. |
| 11 | agentmemory footprint and surface unquantified | 1.0 GB / 185 packages with vendored ONNX runtimes; 7 MCP tools including an unrestricted `memory_save` and a `memory_governance_delete`. |
