# Competitor review and hands-on trials

**Performed:** 2026-07-25
**Purpose:** falsify — cheaply — the assumption that Provalume needs to exist at
all, and steal every good idea that is legally and ethically available.

Two kinds of work are recorded here and kept clearly separated:

- **Verified metadata** — queried live from the GitHub, npm, and PyPI APIs on
  2026-07-25. Reproducible; commands at the end.
- **Hands-on trials** — actually installed and executed locally. Only three
  systems were installed, chosen as the nearest practical neighbours per the
  research report's §9 recommendation.

Where this review contradicts
[`MEMORY_SYSTEM_RESEARCH.md`](MEMORY_SYSTEM_RESEARCH.md), the contradiction is
called out. That report is architectural input, not settled fact, and it is dated
2026-07-25 with its own caveat that no candidate had been installed.

> **No code from any project below was copied, adapted, or vendored into
> Provalume.** Ideas taken at the concept level are attributed in
> [`NOTICE`](../../NOTICE) and in the relevant ADR. Licenses were checked before
> reading with intent to learn: every project reviewed is Apache-2.0 or MIT, both
> of which permit reading and reimplementing ideas; neither permits copying source
> without attribution and license propagation, which is why nothing was copied.

---

## 1. Verified live metadata (2026-07-25)

| Project | Stars | License | Language | Archived | Last push |
|---|---:|---|---|---|---|
| thedotmack/claude-mem | 88,524 | Apache-2.0 | JavaScript | no | 2026-07-23 |
| mem0ai/mem0 | 61,662 | Apache-2.0 | TypeScript | no | 2026-07-25 |
| topoteretes/cognee | 29,300 | Apache-2.0 | Python | no | 2026-07-25 |
| getzep/graphiti | 29,187 | Apache-2.0 | Python | no | 2026-07-24 |
| rohitg00/agentmemory | 25,757 | Apache-2.0 | TypeScript | no | 2026-07-20 |
| steveyegge/beads | 25,639 | MIT | Go | no | 2026-07-25 |
| letta-ai/letta | 23,954 | Apache-2.0 | Python | no | 2026-07-22 |
| asg017/sqlite-vec | 7,929 | Apache-2.0 (dual MIT) | C | no | 2026-05-18 |
| kuzudb/kuzu | 4,020 | MIT | C++ | **yes** | 2025-10-10 |
| qdrant/fastembed | 3,104 | Apache-2.0 | Python | no | 2026-07-22 |
| MinishLab/model2vec | 2,166 | MIT | Python | no | 2026-06-06 |
| riponcm/projectmem | 202 | MIT | Python | no | 2026-07-08 |
| madebyaris/agent-orchestration | 14 | MIT | TypeScript | no | 2026-04-05 |
| bozbuilds/AIngram | 10 | Apache-2.0 | Python | no | 2026-04-27 |

### Corrections to the research report

1. **Beads is 25,639 stars, not ~18,700.** It is also being pushed daily. Its
   position ("git-native agent state, JSONL committed to the repo, database as a
   rebuildable cache") is stronger than the report assumed. This *strengthens* the
   case for Provalume's JSONL interchange design ([ADR-0011](../adr/ADR-0011-jsonl-interchange.md))
   and it means Beads is the closest thing to a strategic template, not a competitor:
   it stores issues, not verified facts.
2. **`madebyaris/agent-orchestration` has 14 stars.** The report presented it as
   "one of the two nearest things to orchestrator + memory". At 14 stars and last
   pushed 2026-04-05 it is a personal project, not a competitive signal. It was
   reviewed for ideas (see §3.3) and is not a threat or an adoption candidate.
   The report over-weighted it.
3. **`ProjectMem` has 202 stars,** not the prominence the report's framing implied.
   Its *idea* — the pre-action gate — remains the single best thing in this review
   and Provalume implements the concept independently. Its low star count does not
   diminish the idea; it does mean the idea is un-owned in practice.
4. **`agentmemory` on PyPI is a different project** from `rohitg00/agentmemory`.
   The PyPI package (`agentmemory` 0.4.8, MIT) requires `chromadb`,
   `psycopg2-binary`, `agentlogger`, `python-dotenv`. The 25.7k-star repo ships on
   npm as `@agentmemory/agentmemory` / `@agentmemory/mcp`. The report conflated
   them in one bullet.
5. **mem0's primary language is now TypeScript,** not Python.
6. **Kuzu's archival is confirmed** (`archived=true`, last push 2025-10-10). The
   report was right, and this remains the clearest single argument against
   depending on someone else's embedded-graph substrate.
7. **The current MCP protocol revision is `2025-11-25`,** not `2025-06-18`. The
   report did not state a revision; this matters because Provalume implements the
   protocol directly. See [ADR-0012](../adr/ADR-0012-mcp-permissions.md).

---

## 2. Hands-on trials

### 2.1 claude-mem — the incumbent

**Installed:** `npm install claude-mem` → `claude-mem@13.12.4`, Apache-2.0.

| Dimension | Finding |
|---|---|
| Install footprint | **41 MB**, 24 npm packages. Modest by npm standards. |
| Runtime prerequisites | Node ≥ 20.12 for the installer. **The runtime commands require Bun**: the CLI help states "Runtime Commands (requires Bun, delegates to installed plugin)". A `repair` subcommand exists to "re-run Bun/uv setup" — so `uv` is also in the loop. |
| LLM requirement | **Mandatory.** Install takes `--provider claude\|gemini\|openrouter` and `--model <id>`. Memory is produced by *compression* — an LLM summarising transcripts. There is no LLM-free write path. |
| Heavier tier | `--runtime server` "brings up Docker pg+redis, generates an API key, injects the IDE MCP config". So the scale-up path is Postgres + Redis + Docker, not SQLite. |
| Local-first viability | Partly. The worker tier is local, but it drives a hosted model to write memory, and telemetry defaults on. |
| Privacy posture | **`telemetry status\|enable\|disable — Manage anonymous telemetry (on by default, opt-out)`.** Opt-out, not opt-in. |
| Branch awareness | Some: an `adopt [--dry-run] [--branch <name>]` command "stamps merged worktrees into parent project". This is the closest thing in the field to branch-aware memory, and it is a post-hoc merge stamp rather than a validity model — there is no notion of a fact being current at one commit and stale at another. |
| Provenance | Session and project scoped. No verification result, no reviewer verdict, no commit SHA as a first-class field. |
| Verification support | None. Nothing in the surface distinguishes "an agent said this" from "a test proved this". |
| MCP experience | An MCP config is injected into the IDE by the installer rather than run standalone. |
| Exportability | Not surfaced in the CLI help. |
| Breadth | Genuinely impressive: `claude-code, cursor, opencode, openclaw, windsurf, codex-cli, copilot-cli, antigravity, goose, …` |

**Ideas worth taking:** the installer's progressive disclosure (one interactive
command that works, with non-interactive flags underneath) is excellent CLI
design and Provalume's `init`/`doctor` follow the same shape. The `adopt --branch`
concept validated that branch-level memory reconciliation is a real user need.

**Why it is not adoptable as Provalume's core:** an LLM in the write path is
disqualifying — it makes memory content non-reproducible across runs, which
destroys the ability to say "this fact was proved by this evidence". Bun and
optional Docker/Postgres/Redis are a footprint Provalume will not inherit.
Telemetry-on-by-default is the opposite of Provalume's privacy stance
([`PRIVACY_MODEL.md`](../security/PRIVACY_MODEL.md)).

**Competitive read:** claude-mem owns cross-CLI session memory and will keep it.
It cannot generate verification provenance without becoming an orchestrator. The
overlap with Provalume is smaller than star counts suggest.

### 2.2 agentmemory (rohitg00) — the loudest claim

**Installed:** `npm install @agentmemory/mcp` → `0.9.28`, Apache-2.0.

| Dimension | Finding |
|---|---|
| Install footprint | **1.0 GB**, 185 packages. |
| Native binaries | `onnxruntime-node` (shipping Linux x64, Linux arm64 *and* darwin arm64 binaries), `sharp`, `@node-rs/jieba`. Also `@xenova` (transformers.js), `@huggingface`, `@opentelemetry`, `express`/`body-parser`. |
| MCP handshake | **Works.** Responded to `initialize` correctly. Notably it answered a `2025-11-25` request with `"protocolVersion":"2024-11-05"` — spec-legal version negotiation, but it is two revisions behind. |
| Tool surface (standalone shim) | **7 tools**: `memory_recall`, `memory_save`, `memory_sessions`, `memory_smart_search`, `memory_export`, `memory_audit`, `memory_governance_delete`. The README badge advertises "53 MCP tools"; the standalone MCP server exposes 7. |
| Permission model | **`memory_save` is an unrestricted write and `memory_governance_delete` is a destructive delete, both exposed to any connected MCP client with no tier, no review, and no separate confirmation.** |
| Provenance | Session-scoped. No verification, review, or commit fields. |
| Marketing posture | README leads with "#1 Persistent memory for AI coding agents based on real-world benchmarks", "95.2% retrieval R@5", "92% fewer tokens", "0 external DBs", "1,428+ tests passing", a Trendshift badge, and 13 translated READMEs. Repo created 2026-02-25. |

**Assessment.** The research report's skepticism is warranted and I will put it
more sharply: a five-month-old project claiming benchmark superiority in a field
where *both* major vendors have had headline numbers corrected downward should not
be treated as an evidence baseline until reproduced independently. The "0 external
DBs" badge is true and simultaneously misleading next to a 1.0 GB install tree
with three platforms' ONNX runtimes vendored in.

**The important finding is the permission model.** `memory_save` +
`memory_governance_delete` exposed to any MCP client is precisely the design
Provalume refuses. An MCP client is, by construction, driven by an LLM that reads
untrusted repository content. Giving it an unrestricted write and a delete is a
memory-poisoning primitive. This directly motivated
[ADR-0012](../adr/ADR-0012-mcp-permissions.md) and
[`MEMORY_POISONING.md`](../security/MEMORY_POISONING.md): Provalume's MCP surface
exposes *propose* (which lands in `quarantined`) and never exposes promote,
invalidate, supersede, or delete.

**Ideas worth taking:** `memory_audit` as a first-class client-callable tool is a
good instinct — auditability should not be a hidden admin feature. Provalume
exposes `provalume audit` on the CLI and keeps it *off* the default MCP surface,
because an audit report leaks scope metadata.

### 2.3 madebyaris/agent-orchestration — reviewed, not adopted

MIT, TypeScript, 14 stars, last pushed 2026-04-05. Reviewed as source rather than
installed, because at that size and staleness an install adds no information.

It is an MCP server over a project-level SQLite database with namespaced memory
(`context` / `decisions` / `findings` / `blockers`). The **namespacing instinct is
right** and it is the one idea taken: separating decisions from findings from
blockers, rather than one undifferentiated `memories` table, is what Provalume's
six-category taxonomy does ([ADR-0004](../adr/ADR-0004-memory-taxonomy.md)) — with
the difference that each category carries its own promotion rule.

It has no verification concept, no trust tiers, no branch or commit semantics, no
lifecycle, and no eval harness. Not an adoption candidate. The research report
over-weighted it as a competitive signal.

---

## 3. Engines reviewed, not installed, and why

Each of these was disqualified on documented, checkable grounds before spending
install time. The report's §3.1 analysis held up; the checks below are what I
verified independently.

| System | Disqualifier, verified |
|---|---|
| **Mem0** (61.7k★) | LLM call in every write path (extract, then ADD/UPDATE/DELETE/NOOP). No LLM-free path exists. Requires a vector store. |
| **Letta** (24.0k★) | An agent *runtime*, not a memory layer. Wants Postgres in production. The flagship repo carries a legacy notice while development moved to a competing coding-agent product. |
| **Zep / Graphiti** (29.2k★) | Needs an external graph database (Neo4j GPLv3, or FalkorDB SSPL) plus an LLM for extraction. Carries a single-vendor CLA, which keeps relicensing open. Zep discontinued its self-hosted Community Edition in April 2025. |
| **Cognee** (29.3k★) | Closest embedded default stack in the field — and its default embedded graph backend, **Kuzu, is archived** (verified `archived=true`, last push 2025-10-10). Quickstart requires an API key; docs recommend 32B+ local models for reliable extraction. |
| **MemOS** | Local tier claims SQLite-only; self-hosted tier wants Neo4j + Qdrant. Vendor-run benchmarks. The exportable part is its governance framing, which Provalume takes at the concept level. |
| **LangMem** | Pre-1.0, thin, practically coupled to LangChain/LangGraph. Extraction needs an LLM. Useful as a design reference only. |

**The cross-cutting disqualifier, restated as Provalume's founding constraint:**
every one of these puts a language model in the memory *write* path. Pointing them
at a local model does not fix it — it makes memory quality depend on whichever
model the user happens to run, and makes the same inputs produce different stored
facts on different days. A system whose entire claim is "these facts were proved"
cannot have a non-reproducible writer. See
[ADR-0007](../adr/ADR-0007-deterministic-writers.md).

---

## 4. Substrate licensing check

Checked because Provalume may depend on these as optional extras.

| Package | Version | License | Verdict |
|---|---|---|---|
| `sqlite-vec` | 0.1.9 | MIT **and** Apache-2.0 (dual) | Compatible. Pre-1.0 with a small maintainer base — pin exactly, keep optional, always fall back to FTS5. |
| `model2vec` | 0.8.2 | MIT | Compatible. Base install is `jinja2, joblib, numpy, safetensors, tokenizers, tqdm` — **no torch** unless the `distill`/`train`/`onnx` extras are requested. Acceptable as an opt-in extra. |
| `fastembed` | latest | Apache-2.0 | Compatible; heavier (pulls onnxruntime). Opt-in extra only. |
| `mcp` (official Python SDK) | 1.28.1 | MIT | Compatible **but rejected as a dependency**: it pulls `starlette`, `uvicorn`, `httpx`, `httpx-sse`, `sse-starlette`, `pyjwt[crypto]`, `jsonschema`, `pydantic-settings`, `python-multipart`, `anyio`. For a stdio server that is a large mandatory surface. Provalume implements MCP stdio JSON-RPC 2.0 against the published specification using only the standard library. See [ADR-0012](../adr/ADR-0012-mcp-permissions.md). |
| `cryptography` | ≥42 | Apache-2.0 **or** BSD-3-Clause | Compatible. Optional extra, for Ed25519 only. |

No GPL, AGPL, SSPL, or CLA-encumbered dependency enters Provalume at any tier.

---

## 5. What Provalume takes, and what it refuses

**Taken (concept level, attributed in [`NOTICE`](../../NOTICE)):**

- Bi-temporal facts with invalidation instead of deletion — Zep/Graphiti's *schema
  idea*, no LLM required.
- The pre-action gate that warns before repeating a known-failed fix — ProjectMem.
  The best single idea in this review.
- Verify-before-promote for procedural memory — Voyager, Memp.
- Recency × usage × relevance with usage-weighted decay — Generative Agents,
  MemoryBank.
- Reciprocal rank fusion over lexical + vector; single-file SQLite with FTS5 and
  optional vectors — AIngram as a published reference architecture.
- Governance metadata as a first-class field — MemOS's framing.
- Git-committed JSONL as portable interchange with the database as a rebuildable
  cache — Beads.
- Progressive-disclosure CLI installation — claude-mem.
- Category namespacing rather than one undifferentiated memory table —
  madebyaris/agent-orchestration.

**Refused, deliberately:**

- An LLM anywhere in the canonical write path.
- Unrestricted write or delete tools exposed to MCP clients.
- Telemetry on by default. Provalume has no telemetry at all.
- An external database, graph store, or vector service as a requirement.
- A vector index as the retrieval gate.
- Benchmarking on LoCoMo, or publishing any superiority claim that has not been
  reproduced from a committed, replayable harness.

## 6. Why Provalume must stay an independent core

1. **No reviewed system stores verification evidence.** Not one carries
   `{verification command, pass/fail, reviewer verdict, commit SHA}` as
   first-class, queryable provenance. That is the whole product.
2. **No reviewed system models branch or commit validity.** claude-mem's
   `adopt --branch` is the closest and it is a merge stamp, not a validity window.
   A fact that was true on a rejected branch is, in every system reviewed, just a
   fact.
3. **Adopting any of them inverts the dependency.** Provalume's differentiators
   would sit on top of a foundation whose roadmap someone else steers — and the
   category's record (Zep CE discontinued, Letta flagship legacy, Kuzu archived,
   Graphiti CLA'd) makes that a live risk, not a hypothetical.
4. **The write path must be reproducible.** Every engine's is not.

## 7. Reproducing this review

```sh
# Live metadata
for r in thedotmack/claude-mem mem0ai/mem0 topoteretes/cognee getzep/graphiti \
         rohitg00/agentmemory steveyegge/beads letta-ai/letta asg017/sqlite-vec \
         kuzudb/kuzu qdrant/fastembed MinishLab/model2vec riponcm/projectmem \
         madebyaris/agent-orchestration bozbuilds/AIngram; do
  printf '%-32s ' "$r"
  gh api "repos/$r" --jq '"\(.stargazers_count)★ \(.license.spdx_id) archived=\(.archived) pushed=\(.pushed_at[0:10])"'
done

# claude-mem trial
mkdir cm && cd cm && npm init -y && npm install claude-mem
du -sh node_modules && npx --no-install claude-mem --help

# agentmemory trial
mkdir am && cd am && npm init -y && npm install @agentmemory/mcp
du -sh node_modules && find node_modules -name '*.node'
{ echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}'
  echo '{"jsonrpc":"2.0","method":"notifications/initialized"}'
  echo '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'; sleep 6; } \
  | npx --no-install @agentmemory/mcp
```

Trials were run in a scratch directory outside any project tree. Nothing was
installed globally and no IDE configuration was modified — `claude-mem install`
and `agentmemory`'s installer both write into IDE settings, so neither installer
was run; only the CLI surface and the MCP protocol surface were exercised.
