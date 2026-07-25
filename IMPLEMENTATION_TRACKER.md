# Implementation tracker

**Project:** Provalume — verified, git-aware memory for autonomous software agents
**Session started:** 2026-07-25
**Status:** v0.1.0 released — published to PyPI through OIDC Trusted Publishing on 2026-07-25

This file records what was actually done, what was verified, what was corrected,
and what was deliberately not done. It is not a plan; it is evidence.

---

## Status by phase

| Phase | Status | Evidence |
|---|---|---|
| 0 — Inventory, name clearance, competitor trials | done | `docs/research/NAME_CLEARANCE.md`, `COMPETITOR_TRIALS.md`, `RESEARCH_VALIDATION.md` |
| 1 — Threat, trust, poisoning, privacy models | done | `docs/security/` (4 documents, written before the engine) |
| 2 — ADR-0001..0018 | done | `docs/adr/` (18 records, each with a Consequences section) |
| 3 — Repository foundation | done | `pyproject.toml`, `src/` layout, LICENSE, NOTICE, CI, design tokens |
| 4 — Immutable event journal | done | `store/journal.py`, `store/db.py`, `store/migrations.py` |
| 5 — Memory model | done | `schemas/memories.py`, six categories with per-type ceilings |
| 6 — Deterministic writers | done | `writers/` — five modules, no LLM anywhere |
| 7 — Git- and branch-aware truth | done | `store/gitinfo.py`, tested against real rebase/cherry-pick/merge |
| 8 — Retrieval and explainability | done | `retrieval/lexical.py`, `ranking.py`, `digest.py` |
| 9 — Pre-action warning gate | done | `retrieval/preflight.py` |
| 10 — JSONL interchange | done | `interchange/jsonl.py`, `signatures.py` |
| 11 — Optional vector retrieval | done | `retrieval/vectors.py`, experimental, off by default |
| 12 — SDK, CLI, MCP | done | `sdk/client.py`, `cli/main.py`, `mcp/` (stdlib, no SDK dependency) |
| 13 — Demo | done | `provalume demo` — 12 beats, offline, real engine |
| 14 — Evaluation harness | done | 20 scenarios, baseline committed |
| 15 — Orkestra integration | **done** — draft PR [orkestra#6](https://github.com/andyyaro/orkestra/pull/6), all 7 CI jobs green, not merged | `integrations/orkestra.py`, `generic.py` |
| 16 — Tests and security gates | done | 664 passed, 1 skipped; all gates green |
| 17 — Documentation | done | 30+ documents |
| 18 — GitHub repository | **done** — CI and CodeQL green, 0 open code-scanning alerts | — |
| 19 — v0.1.0 release | **done** — annotated tag `v0.1.0`, GitHub release with both artifacts | — |
| 20 — PyPI Trusted Publishing | **done** — published through OIDC, no API token; both artifacts carry PEP 740 attestations | `publish-to-pypi.yml` |

---

## Verified gate results

Run on 2026-07-25 against the working tree.

| Gate | Result |
|---|---|
| `ruff check src tests` | All checks passed |
| `mypy` (strict) | Success: no issues in 59 source files |
| `bandit -c pyproject.toml -r src` | 0 findings (high 0, medium 0, low 0) |
| `pip-audit` | No known vulnerabilities |
| `pytest tests` | **664 passed, 1 skipped** |
| `provalume eval` | **20/20 scenarios passed** |
| `docs/design/contrast_check.py` | All documented contrast constraints hold |
| Deterministic-core branch coverage | **87.8%** (target 85%) |
| `twine check --strict dist/*` | PASSED for wheel and sdist |
| Wheel install + smoke | 13 packages, `provalume --version`, `demo` OK |
| Sdist install + `doctor` | All checks passed |

The one skip is `test_verification_without_the_backend_raises_rather_than_returning_false`,
which can only run when the `signatures` extra is *absent*; it is installed in
this environment.

### Eval baseline

Committed at `evals/results/baseline/results.json`.

| Metric | Result |
|---|---|
| Poisoning success rate | **0/5 (0%)** — target zero |
| Cross-scope leakage | **0/3 (0%)** |
| Stale-memory rate | **0/1 (0%)** |
| False warnings | **0/5 (0%)** |
| Recall precision | 2/2 (100%) |
| Recall coverage | 1/1 (100%) |
| Procedure reuse | 1/1 (100%) |
| Retrieval latency | p95 1.7 ms |

Denominators are small because these are targeted scenarios rather than a corpus.
Reported that way deliberately — see `docs/reference/BENCHMARKS.md`.

---

## Research validation — corrections made

The research report was treated as architectural input, not settled fact. Eleven
claims were checked and corrected. Full detail in
`docs/research/RESEARCH_VALIDATION.md`.

| # | Report said | Verified |
|---|---|---|
| 1 | Build inside Orkestra first, extract later | **Deliberate deviation.** Standalone from day one; trade-off documented. |
| 2 | MCP revision unstated | Current revision is **`2025-11-25`**, not `2025-06-18` |
| 3 | Beads ~18.7k stars | **25,639** |
| 4 | `madebyaris/agent-orchestration` a notable near-competitor | **14 stars.** A personal project; the report over-weighted it. |
| 5 | `agentmemory` as one project | **Two unrelated projects** share the name across npm and PyPI |
| 6 | ProjectMem prominent | **202 stars.** The idea is excellent and effectively un-owned. |
| 7 | mem0 Python-centric | Primary language is now **TypeScript** |
| 8 | Orkestra distribution name unstated | **`orkestra-runtime`**, v0.4.4 |
| 9 | Official MCP Python SDK the obvious choice | **Rejected**: 10+ transitive deps including an HTTP stack, for a stdio server |
| 10 | claude-mem footprint unquantified | **41 MB / 24 packages**, requires Bun, optional Docker+Postgres+Redis, **telemetry on by default** |
| 11 | agentmemory footprint unquantified | **1.0 GB / 185 packages** with vendored ONNX runtimes; 7 MCP tools including an unrestricted `memory_save` and a `memory_governance_delete` |

### Verified against Orkestra source (read-only)

| Claim | Result |
|---|---|
| One prompt choke point at `_render_brief()` | **Confirmed** — `kernel/scheduler.py:757`, called at `:691` |
| `commit_workspace()` runs `git add -A` | **Confirmed** — `workspace/worktrees.py:100` → `add_all_and_commit()` |
| Tables: runs, tasks, attempts, events, decisions, observations, ledger, workspaces, usage_log | **Confirmed** |
| Review verdicts live only in `attempts.result` JSON; no `reviews` table | **Confirmed** — the adapter takes verdicts as an explicit call |
| Python ≥3.12, mypy strict, Apache-2.0, stdlib sqlite3 WAL, no ORM | **Confirmed** |

### Deliberately not verified

Antigravity's native `AGENTS.md` support and the Claude adapter's
`--setting-sources user` behaviour inside worktrees. Verifying either requires
invoking real vendor CLIs against a real worktree, which would mean mutating the
active Orkestra checkout (prohibited this session) or consuming vendor quota for
a result that only gates an optional feature. **Provalume 0.1.0 depends on
neither** — the prompt splice is the primary path.

---

## Name clearance

**Verdict: clear.** Checked 2026-07-25.

| Namespace | Result |
|---|---|
| PyPI `provalume` | available (404) |
| npm `provalume` | available (404) |
| crates.io | 0 results |
| GitHub repo/user/search | 0 results |
| Nearest trademarks | PROVALUS (BPO/IT staffing), PROVALIS (text analytics), PROVALYTICS (marketing SaaS), PROVAL (real-estate) — none in software agent memory, provenance, or developer tooling |
| `provalume.com` | **Registered 2026-07-22** — a Japanese **HYROX fitness-gym directory**. Not a software, AI, or developer-tooling product; not a material collision. `.dev`, `.io`, `.org`, `.ai` all unregistered. |

Full record: `docs/research/NAME_CLEARANCE.md`.

---

## Competitor trials — hands-on

Three systems installed and exercised, not merely read about.

| System | Footprint | Finding |
|---|---|---|
| **claude-mem** 13.12.4 | 41 MB, 24 packages | Requires Bun for runtime; LLM provider mandatory (`--provider claude\|gemini\|openrouter`); optional Docker+Postgres+Redis tier; **telemetry on by default, opt-out**. Has `adopt --branch` — the closest thing in the field to branch awareness, and it is a merge stamp rather than a validity model. |
| **@agentmemory/mcp** 0.9.28 | **1.0 GB, 185 packages** | Vendored `onnxruntime-node` for three platforms, `sharp`, `@node-rs/jieba`. MCP handshake works; negotiates `2024-11-05`. **7 tools including an unrestricted `memory_save` and `memory_governance_delete`** exposed to any client — the design that motivated ADR-0012. README claims "0 external DBs" alongside that install tree. |
| **madebyaris/agent-orchestration** | 14 stars | Reviewed as source. Namespacing instinct is right; no verification, trust tiers, branch semantics, or lifecycle. Not an adoption candidate. |

No code was copied from any project. Ideas taken at the concept level are
attributed in `NOTICE`. Full record: `docs/research/COMPETITOR_TRIALS.md`.

---

## Design decisions

All 18 ADRs were written **before** the corresponding implementation. Each has a
Consequences section listing what the decision makes worse.

The load-bearing ones:

- **ADR-0002** — events are the source of truth; everything else is a rebuildable
  projection. The hash chain is deliberately *local* tamper-evidence, not a global
  ledger.
- **ADR-0005** — eight trust states in two shapes: a ranked five-rung ladder plus
  three unranked terminal states. Asking "is `rejected` more trusted than
  `observed`?" is a category error, so the model does not answer it.
- **ADR-0007** — no LLM in any canonical path. The reason is specific:
  non-reproducibility makes "proved by this evidence" unverifiable.
- **ADR-0008** — every ranking constant published with its reasoning. Defaults are
  *reasoned, not fitted*, and that is stated.
- **ADR-0012** — dangerous MCP operations are **absent**, not disabled. A disabled
  tool is one misconfiguration from enabled.
- **ADR-0016** — global memory does not exist in 0.1.0, because cross-project
  leakage is the one Critical-rated confidentiality threat.

---

## Bugs found and fixed during implementation

Found by tests and by the eval harness, not by inspection.

| # | Bug | Found by | Fix |
|---|---|---|---|
| 1 | Review approval and integration stamped onto **every** record sharing a task — a gotcha read "approved by reviewer-2" and, after merge, "integrated" | Manual pipeline trace | Introduced claim types vs record types; verdicts attach by association only to claims, or to a record type whose subject the reviewer named |
| 2 | Import trusted a record's **declared** `payload_hash`, so a tampered payload with a stale hash passed as a harmless duplicate | **Eval scenario 14** | Hash is recomputed on import; a mismatch is a rejection naming the discrepancy |
| 3 | A short subject (`ci`, `pm`, `db`) produced an empty subject key, silently disabling supersession and contradiction detection for it | Integration test | Length filter relaxes when it would otherwise empty the key |
| 4 | Decisions could not climb the ladder: no command exists to verify, so `observed → verified` always refused | SDK smoke test | A human decision event is its own evidence at each rung; rungs are still walked and recorded |
| 5 | `RedactionReport.count` shadowed `tuple.count`, replacing a method with an int | mypy strict, then a test | Renamed to `matches`; `to_dict()` still emits `count` |
| 6 | Digest warnings duplicated — membership tested against the wrong string | Manual output review | Roll-up keyed on a stable tag |
| 7 | `~/.ssh/id_rsa` passed path confinement as a subdirectory literally named `~` | Security test | `expanduser()` before resolution |
| 8 | Fingerprint picked the failing source line over the exception it raised, so the same source line with different exceptions collided | Manual signature testing | Two-tier matching: a line that *declares* an error wins |
| 9 | An over-long query raised instead of truncating | Security test | Truncated — a long query is a paste, not an attack |
| 10 | Live-path writes never advanced the projection watermark, so `audit` always warned "projections are behind" | SDK smoke test | `apply()` advances it |
| 11 | `payload.get("branch", event.branch)` returned `""` when the key was present-but-empty, so integration matched nothing | SDK smoke test | `or` chain instead of a dict default |
| 12 | Three-rung promotions printed scrambled — same-millisecond ULIDs have arbitrary relative order | Demo output | Monotonic ID factory for transitions |
| 13 | The Orkestra hook read `Assignment.agent`, a field that does not exist — and because the guard wrapped only the adapter call, the `AttributeError` from *building the arguments* escaped and crashed the run | Orkestra's own suite, 3 pre-existing tests | Correct field, and the guard became a context manager wrapping the whole interaction |
| 14 | Orkestra's `run.completed` record was written after a `finally` had already closed the client, so the best-effort guard swallowed it and it never recorded | Reading the diff; then a regression test verified by reintroducing the bug | Record before the `finally` closes |

| 15 | The Orkestra hook iterated the *requested* verify commands rather than the results. Verification stops at the first failure, so a command that passed — or never ran at all — was recorded as failed, manufacturing gotchas and then false warnings | **Dogfooding**, then a regression test | Iterate `outcome.results`, which carries each command's own verdict and exit code |
| 16 | The recorded excerpt was `outcome.summary`, a one-line headline containing no error text. Every failure of a given command therefore produced the *same* failure signature, so the gate would warn about an unrelated failure | **Dogfooding** — demonstrated by showing two unrelated failures collapsing to one signature | Excerpt now carries captured stdout/stderr |
| 17 | `provalume events/memories/recall` printed *nothing* when the project id did not match, which is indistinguishable from "the integration recorded nothing" | **Dogfooding** — it cost a long misdiagnosis of exactly that kind | Empty results now name the projects the database actually holds |

| 18 | `resolves_signature` was dead code: the projector read it to link a fix to the failure it resolved across runs, and nothing anywhere could write it. The only reachable path inferred resolution within one task or run, which the real recovery path (block → escalate → fix in a *later* run) never satisfies | **Dogfooding round 2** | `record_verification()` accepts it; the integration supplies it |
| 19 | The Orkestra hook filed a review verdict against the *reviewer's* freshly created attempt, and recorded verification with no attempt at all. Provalume associates evidence by attempt, so the two halves never met: nothing was stamped, and **no memory could climb past `verified` in a real run** | **Dogfooding round 2** | Both now carry the attempt under review |
| 20 | A resolved failure read exactly like an open one — the warning still opened "A similar approach failed previously" while carrying what fixed it | **Dogfooding round 2** | Resolved matches say so in the headline |
| 21 | The gate could not be consulted without emitting `warning.shown`, so an internal lookup inflated the count warning-usefulness is measured from | **Dogfooding round 2** — I introduced it, then measured it | `preflight(record=False)` |

Bugs 13 and 14 are the same lesson from two directions. Swallowing errors on
memory writes is the right policy — a memory fault must not fail an
orchestration run — but it is also what let a real defect stay invisible. So the
wiring needs tests that assert a write **happened**, not merely that nothing
raised.

---

## Security work

- Threat model with **26 threats**, controls, and a residual-risk section that
  states what is *not* solved.
- **232 security tests** across trust invariants, MCP surface, injection, path
  traversal, redaction, no-network, and the integration boundary.
- The MCP tool-name set is **pinned by test**: adding a promotion tool fails CI.
- `tests/security/test_no_network.py` asserts, via AST rather than grep, that no
  module imports a network-capable library and that the three required
  dependencies are exactly pydantic, typer, rich.
- Poisoning success rate measured at **0/5** against adversarial fixtures.

---

## Deliberate deviations from the brief

| Deviation | Reasoning |
|---|---|
| Standalone rather than built-inside-Orkestra-first | Directed. The dependency direction comes out right by construction; the cost — no production-mined requirements — is the first item in `LIMITATIONS.md`. |
| MCP implemented without the official SDK | 10+ transitive dependencies including an HTTP stack, for a stdio server. Would also make the "no network code" claim unverifiable. |
| No LongMemEval-V2 score claimed | Running it is out of scope for 0.1.0. A LongMemEval-V2-*style* harness ships instead, and says so. |
| Vendor context-file pickup unverified | Requires real vendor CLIs; the primary path depends on neither unverified fact. |

---

## Known blockers

**Phase 20 is complete.** The pending publisher was configured, the tag
`v0.1.0` triggered `publish-to-pypi.yml`, the protected `pypi` environment gate
was approved by the repository owner, and both artifacts were published through
GitHub OIDC. No PyPI API token was ever created. Both the wheel and the sdist
carry PEP 740 attestations naming `andyyaro/provalume` and
`publish-to-pypi.yml`, verifiable at
`https://pypi.org/integrity/provalume/0.1.0/<filename>/provenance`.

---

## Deferred, with reasons

| Item | Why |
|---|---|
| Global cross-project memory | Riskiest feature, no data to design its promotion policy safely (ADR-0016) |
| Content-level equivalence after rebase/cherry-pick | Degrades to `uncertain` today, which is honest |
| LLM-optional idle-time distillation | Would be additive and capped; not needed for 0.1.0 |
| Incremental export | Full serialisation is fine at current sizes |
| Web dashboard | `explain` already answers the question |
| Automated team sync | Needs conflict resolution that cannot be done safely today |
| Real lexical-vs-hybrid comparison | Needs a corpus large enough to mean anything |

---

## Confirmations

- **`~/Downloads/Orkestra` was never mutated.** Only read commands were run there:
  `git status --porcelain`, `git rev-parse`, `git remote -v`, `git tag --list`,
  `find`, `grep`, `sed -n`, `cat`. No edit, stage, commit, stash, reset, clean,
  checkout, branch switch, worktree, lockfile change, migration, or test run.
- **No unrelated user files were overwritten.** All work is confined to
  `~/Downloads/Provalume`, with scratch files in the session scratchpad.
- **No destructive Git action was taken.** No tag was moved or reused.
- **No benchmark, test, or publication result in this file is fabricated.** Every
  figure above was produced by a command run in this session.
