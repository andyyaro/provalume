# Session checkpoint — 2026-07-25

Written before context compaction so the next context can resume without
re-deriving anything. Everything here was produced by a command actually run in
this session; nothing is estimated.

**Companion document:** [`IMPLEMENTATION_TRACKER.md`](IMPLEMENTATION_TRACKER.md)
carries the phase-by-phase evidence, the research corrections, and the bug log.
This file is the resume point.

---

## 1. Objective

Design, implement, test, document, publish, and release a standalone open-source
project called **Provalume**: local-first, verification-grounded, git-aware
memory for autonomous software agents.

| Identifier | Value |
|---|---|
| GitHub | `andyyaro/provalume` — https://github.com/andyyaro/provalume |
| PyPI distribution | `provalume` |
| Import package | `provalume` |
| CLI | `provalume` |
| MCP server name | `provalume` |
| Project-local state | `.provalume/` |
| Default database | `.provalume/provalume.db` |
| Tagline | **Facts your agents proved, not things they said.** |
| Licence | Apache-2.0, no CLA, DCO optional |

Positioning: **not** another conversational-memory framework. It differentiates
on verification-grounded promotion, independent-review provenance, branch-aware
truth, deterministic writes, failed-attempt memory, explainable retrieval, and
memory-poisoning resistance.

## 2. Current state

**Phases 0–19 complete. Phase 20 (PyPI publish) is the remaining work.**

| Phase | State |
|---|---|
| 0 Inventory, name clearance, competitor trials | done |
| 1 Threat / trust / poisoning / privacy models | done |
| 2 ADR-0001..0018 | done |
| 3 Repository foundation | done |
| 4 Immutable event journal | done |
| 5–6 Memory model + deterministic writers | done |
| 7 Git- and branch-aware truth | done |
| 8–9 Retrieval, explainability, digest, preflight | done |
| 10–11 JSONL interchange + optional vectors | done |
| 12 SDK, CLI, MCP | done |
| 13–14 Demo + 20-scenario eval harness | done |
| 16 Tests and security gates | done |
| 17 Documentation | done |
| 18 GitHub repository | **done — published, CI green** |
| 19 v0.1.0 release | **tag not yet created** ← next |
| 15 Orkestra integration | adapter done; **Orkestra-side wiring in progress** |
| 20 PyPI Trusted Publishing | **unblocked, waiting on the tag** |

### Verified gate results

Run against the working tree in this session:

| Gate | Result |
|---|---|
| `ruff check src tests` | All checks passed |
| `mypy` (strict) | Success, 59 source files |
| `bandit -c pyproject.toml -r src` | **0 findings** (high 0, medium 0, low 0) |
| `pip-audit` | No known vulnerabilities |
| `pytest tests` | **664 passed, 1 skipped** |
| `provalume eval` | **20/20 scenarios passed** |
| `docs/design/contrast_check.py` | All contrast constraints hold |
| Deterministic-core branch coverage | **87.8%** (target 85%) |
| `twine check --strict dist/*` | PASSED, wheel and sdist |
| Wheel + sdist install and smoke | 13 packages, `--version`/`demo`/`doctor` OK |
| GitHub Actions CI on `main` | **success** at `fcb8b169` |
| CodeQL | **success**; 17 alerts → 3 → 0 expected on next scan |

The single skip is `test_verification_without_the_backend_raises_rather_than_returning_false`,
which can only run when the `signatures` extra is *absent*; it is installed here.

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

Denominators are small because these are targeted scenarios, not a corpus. Read
`docs/reference/BENCHMARKS.md` before quoting any of them.

## 3. Repository and git status

### `~/Downloads/Provalume` — the project

- Remote: `https://github.com/andyyaro/provalume.git`, branch `main`, pushed.
- Commits so far:

| SHA | Subject |
|---|---|
| `569b2e2` | Provalume v0.1.0: initial implementation (155 files) |
| `0519074` | fix: pin the initial branch name in Git test fixtures |
| `058aa62` | security: fix two ReDoS vulnerabilities |
| `288f30f` | security: fix a second, quadratic ReDoS |
| `43a6a72` | chore: clear remaining code-scanning notes |
| `fcb8b16` | fix: clear every code-scanning alert + a flaky interchange test |
| *(uncommitted at checkpoint time)* | last three code-scanning notes + this file |

- **No tag exists yet.** `v0.1.0` has not been created.

### `~/Downloads/Provalume-Orkestra-Integration` — the integration clone

- Cloned from `https://github.com/andyyaro/orkestra.git` (remote `main`,
  `8ababee`, v0.4.4).
- Branch: `provalume-memory-integration`. **Nothing committed or pushed yet.**
- Has its own `.venv` with Orkestra plus the local Provalume installed.

### `~/Downloads/Orkestra` — the other session's checkout

**READ-ONLY. Never mutated, and must stay that way.** Only these were ever run
there: `git status --porcelain`, `git rev-parse`, `git remote -v`,
`git tag --list`, `find`, `grep`, `sed -n`, `cat`.

It has since moved to branch `fleet2-fixes-v0.4.5` — another session is working
there. The integration is correctly based on remote `main`, not on that branch.

## 4. What was built

`src/provalume/` — 59 modules:

| Area | Modules |
|---|---|
| Schemas | `events`, `memories`, `trust`, `scope`, `provenance`, `retrieval` |
| Store | `db`, `migrations`, `journal`, `repository`, `projections`, `fts`, `gitinfo`, `integrity` |
| Policy | `admission`, `promotion`, `invalidation`, `scope`, `poisoning` |
| Writers | `verification`, `reviews`, `decisions`, `runs`, `failures` |
| Retrieval | `lexical`, `ranking`, `digest`, `preflight`, `vectors` |
| Interchange | `jsonl`, `hashing`, `signatures` |
| Interfaces | `sdk/client`, `cli/main`, `cli/theme`, `mcp/server`, `mcp/permissions` |
| Integrations | `generic`, `orkestra` |
| Other | `redact`, `errors`, `_ids`, `_time`, `demo/scenario`, `evals/replay`, `evals/metrics` |

`tests/` — 665 tests across `unit/`, `integration/`, `security/`, `e2e/`.
Security suites cover trust invariants, the MCP surface, injection and path
traversal, redaction, no-network, ReDoS, and the integration boundary.

`docs/` — 18 ADRs, 4 security documents, 12 reference documents, architecture
overview, quickstart, release procedure, brand guide, design tokens, and the
three research documents (name clearance, competitor trials, research
validation).

## 5. Critical constraints that must survive

These are load-bearing. Violating one silently breaks the product's claim.

### Never
1. **No LLM in any canonical path** — write, read, ranking, or promotion.
2. **Agents never promote.** No SDK path, CLI path, or MCP tool.
3. **The MCP surface exposes no promote / invalidate / supersede / reject /
   delete / scope-move / rebuild / import / export / audit tool.** Absent, not
   disabled. Pinned by `tests/security/test_mcp_surface.py`.
4. **Payload never influences its own trust state.** `source` is structural.
5. **No cross-project or global promotion.** `global` scope is unreachable.
6. **Vectors never authorise**, only reorder.
7. **Semantic records are never served as current truth without landed history.**
8. **Never mutate `~/Downloads/Orkestra`.**
9. **Never move or reuse a tag.** Never publish from a local machine.
10. **No telemetry, no network code.** Asserted by `tests/security/test_no_network.py`.
11. **Never weaken a safety check to make a test pass.**

### Always
- Redaction runs **before** the durable write; hashing after it.
- Events are append-only, enforced by database triggers.
- Every trust transition records its **named policy rule** and evidence —
  including refusals.
- Every digest opens with the untrusted-data banner.
- Retrieval filters authorise; scoring only reorders.
- Writers are pure functions of their event, so `rebuild` reproduces byte-identically.
- No benchmark comparison against another system is published.

## 6. Unresolved / in progress

### a. Orkestra integration — the active work

In `~/Downloads/Provalume-Orkestra-Integration` on branch
`provalume-memory-integration`, **uncommitted**:

| File | Change |
|---|---|
| `src/orkestra/memory.py` | **new** — the optional bridge. `open_memory()` returns `None` when Provalume is absent, disabled, or its database will not open. Every write is best-effort; every read degrades to `""`. |
| `src/orkestra/schemas/config.py` | **new** `MemoryConfig` section (`enabled`, `brief_budget_chars`, `preflight`), wired onto `ProjectConfig`. Needed because `ProjectConfig` sets `extra="forbid"`. |
| `src/orkestra/kernel/scheduler.py` | imports `Memory`/`open_memory`; `self._memory` field; `_memory_sections()` helper; digest + preflight appended in `_render_brief`; verification recorded in `_verify`; review recorded inside `_review` (where the reviewer's identity is known); integration recorded after merge; memory opened in `execute()` and closed in `finally`. |
| `tests/test_memory.py` | **new** — optionality, best-effort writes, digest budget, banner presence, preflight, full ladder. |

**Its test suite has not finished running yet** (started as background task
`bcmwly8vb`; Orkestra's suite is slow). Nothing has been committed there.

### b. Code scanning

17 alerts at first scan → 3 after the last push → **0 expected** after the
uncommitted `__all__` additions and the journal initialiser removal. Verify with:

```sh
gh api repos/andyyaro/provalume/code-scanning/alerts --jq '[.[]|select(.state=="open")]|length'
```

### c. Dependabot pull requests

Several are open. Their CI failed because they branch from the **pre-fix**
commit and hit the `git init` branch-name bug fixed in `0519074`. Rebasing them
on current `main` should turn them green; they are not a code problem.

## 7. Exact next steps

### Step 1 — commit this checkpoint and the pending fixes

```sh
cd ~/Downloads/Provalume
git add -A && git commit && git push origin main
```

### Step 2 — confirm CI and code scanning are clean

```sh
gh run list --repo andyyaro/provalume --branch main --limit 4
gh api repos/andyyaro/provalume/code-scanning/alerts --jq '[.[]|select(.state=="open")]|length'   # want 0
```

### Step 3 — tag and release v0.1.0

Do **not** tag until CI is green on the exact commit being tagged.

```sh
cd ~/Downloads/Provalume
uv run pytest tests -q && uv run provalume eval && uv run ruff check src tests && uv run mypy
git tag -a v0.1.0 -m "provalume v0.1.0 — verified, git-aware memory for agents"
git push origin v0.1.0
```

The tag triggers `.github/workflows/publish-to-pypi.yml`: verify → build →
publish (OIDC) → GitHub release.

### Step 4 — approve the deployment

**This needs a human.** The `pypi` environment has required reviewers
(`andyyaro`) and a `v*` tag rule. The publish job will pause until approved at:

```
https://github.com/andyyaro/provalume/actions
```

PyPI's pending publisher is **already configured** — confirmed by the operator:
project `provalume`, owner `andyyaro`, repository `provalume`, workflow
`publish-to-pypi.yml`, environment `pypi`.

### Step 5 — verify the fresh install

```sh
uv tool install provalume
provalume --version && provalume doctor && provalume demo
provalume --help && provalume serve-mcp --help
```

Confirm five things agree: `pyproject.toml`, the git tag, the GitHub release,
the PyPI release, and `provalume --version` from a clean environment.

### Step 6 — finish the Orkestra draft PR

```sh
cd ~/Downloads/Provalume-Orkestra-Integration
.venv/bin/python -m pytest tests -q          # must pass
.venv/bin/ruff check src tests && .venv/bin/mypy

git fetch origin && git rebase origin/main   # rebase onto latest remote main
.venv/bin/python -m pytest tests -q          # rerun after the rebase

git add -A && git commit
git push -u origin provalume-memory-integration
gh pr create --repo andyyaro/orkestra --draft \
  --title "Optional verified memory via Provalume" --body-file <(...)
```

**Draft only. Do not merge. Do not release a new Orkestra version.**

## 8. Environment

| | |
|---|---|
| Working dir | `/Users/andyyaro/Downloads/Provalume-Session` (session scratch; the project is `~/Downloads/Provalume`) |
| Python | 3.14.6 system; project venv on 3.12.6 |
| Tools | `uv` 0.11.29, `gh` authenticated as `andyyaro` (scopes: gist, read:org, repo, workflow) |
| Provalume venv | `~/Downloads/Provalume/.venv` — run gates as `.venv/bin/<tool>` |
| Orkestra clone venv | `~/Downloads/Provalume-Orkestra-Integration/.venv` |

## 9. Two things worth not relearning

**The largest known weakness** is that Provalume has not been dogfooded on
production runs. Its schema comes from literature, a competitor review, and a
synthetic eval harness — not from mined production failure frequencies. This is
stated first in `docs/reference/LIMITATIONS.md` and must not be quietly dropped
from the README or the release notes.

**Two ReDoS vulnerabilities were found and fixed** during publication, both in
patterns that parse attacker-influenced text. The first was caught by CodeQL; the
second by the regression suite written for the first. The lesson recorded in
`tests/security/test_redos.py`: adversarial fixtures must include **the literals
the pattern itself searches for**, which the original suite omitted.
