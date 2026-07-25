# Name clearance: Provalume

**Check performed:** 2026-07-25
**Name under review:** `Provalume` (display), `provalume` (identifier)
**Pronunciation:** PROV-uh-loom
**Scope of concern:** software, agent memory, AI tooling, developer tooling,
provenance, verification, evidence management, automation.

**Verdict: CLEAR.** No exact collision and no materially confusing collision was
found in any relevant field. One unrelated `.com` domain is in use by a Japanese
fitness-gym directory; it is not a software, AI, or developer-tooling product and
does not create confusion in this project's category. Proceeding under the name
`Provalume`.

This document records what was checked and what was found so the decision can be
re-audited rather than taken on trust. Every check below was run directly, not
recalled.

---

## 1. Exact-identifier availability

| Namespace | Query | Result | Method |
|---|---|---|---|
| PyPI | `provalume` | **available** (HTTP 404 on `/pypi/provalume/json`) | PyPI JSON API |
| PyPI | `provolume` (typo-adjacent) | available (404) | PyPI JSON API |
| npm | `provalume` | **available** (HTTP 404 on registry) | npm registry API |
| crates.io | `provalume` | **0 results** | crates.io search API |
| GitHub repo | `andyyaro/provalume` | **does not exist** (404) | GitHub REST API |
| GitHub user/org | `provalume` | **does not exist** (404) | GitHub REST API |
| GitHub repo search | `provalume` | **0 total results** | `search/repositories` |
| GitHub user search | `provalume` | **0 total results** | `search/users` |

No identifier squatting, no prior release, no name-adjacent package that a user
could install by mistake.

## 2. Trademark and company-name review

No registered or applied-for mark reading `PROVALUME` was found. The nearest
existing marks and companies, with the reason each is not a material conflict:

| Name | What it is | Class / field | Assessment |
|---|---|---|---|
| **PROVALUS** | US business-process-outsourcing and IT-staffing company (Optomi, LLC). Registered US mark, reg. no. 5291063, in the computer & software services class. | BPO / IT staffing services | **Nearest mark.** Different suffix (`-us` vs `-ume`), different pronunciation (pro-VAL-us vs PROV-uh-loom), and a different commercial activity: PROVALUS sells outsourced human labour and IT support services, not a software product, and does not operate in agent memory, developer tooling, or provenance. Not a source-confusion risk for an open-source Python library and CLI. Flagged here so the record is honest: this is the one mark that shares the `Prova-` stem in an adjacent international class. |
| **PROVALIS** (Provalis Research) | Commercial text-analytics and qualitative-data-analysis software (QDA Miner, WordStat). | Text analytics software | Different name (`-alis` vs `-alume`), different field (qualitative research analytics, not agent memory or verification provenance). No overlap in users or distribution channel. |
| **PROVALYTICS** | SaaS for marketing-data analytics. | Marketing analytics | Different name and field. |
| **PROVAL** | Real-estate assessment and valuation software (Manatron, Inc.), filed 1994. | Real-estate assessment | Different name and field; long-established in an unrelated vertical. |

None of these is an exact match. None operates in software agent memory, AI
developer tooling, provenance, verification, evidence management, or automation.

**Limitation, stated plainly:** this is a availability-and-similarity review
performed against public search results and public trademark records. It is not a
legal opinion and it is not a full trademark clearance search. `trademarks.justia.com`
returned HTTP 403 to automated fetching, so USPTO/WIPO findings here rest on
public search-engine results surfacing those registrations rather than on a direct
query of the USPTO or WIPO databases. Anyone intending to assert or register
`Provalume` as a mark should commission a professional clearance search. For
publishing an Apache-2.0 open-source project under this name, the review above is
proportionate.

## 3. Web and domain presence

| Check | Result |
|---|---|
| Web search, `"Provalume"` exact | **No exact-match results.** Search engines substituted `Provalus`, `Provalis`, and unrelated chemistry terms ("volumetric flask", "volume of distribution"), which is the signature of a term with no established presence. |
| Web search, `"Provalume" software OR company OR trademark` | No exact-match results; returned the adjacent marks in §2. |
| `provalume.com` | **Registered and live.** Created 2026-07-22 via XServer Inc. (JP registrar), Caddy + Next.js site. |
| `provalume.dev` | no DNS — appears unregistered |
| `provalume.io` | no DNS — appears unregistered |
| `provalume.org` | no DNS — appears unregistered |
| `provalume.ai` | no DNS — appears unregistered |

### On `provalume.com`

The site's own structured data identifies it as:

```json
{"@type":"WebSite","name":"Provalume","alternateName":"HYROX対応ジム全国一覧"}
```

Title: `Provalume｜HYROXのジム探しと大会情報`. Description (translated): a
comparison guide to HYROX-compatible gyms in Japan, with competition guides and
news.

It is a Japanese-language **fitness-gym directory** for HYROX competitions,
registered three days before this check. Applying the standing rule — replace the
name only for an exact or materially confusing collision *in software, memory, AI,
developer tooling, provenance, verification, evidence, or automation* — this is
not a collision. There is no overlap in category, audience, distribution channel,
or search intent between a Japanese gym directory and a Python library for agent
memory provenance.

Practical consequence: the `.com` is unavailable for project use. This is a
branding inconvenience, not a naming blocker. `provalume.dev` is the natural home
if a documentation site is ever wanted, and v0.1.0 ships without one — the
canonical URL is the GitHub repository.

## 4. Confusing-similarity screen against this project's own field

The names Provalume could realistically be confused with, inside agent
memory / AI developer tooling, were screened directly. None is close:

`claude-mem`, `Mem0`, `mem0ai`, `Letta`, `MemGPT`, `Zep`, `Graphiti`, `Cognee`,
`MemOS`, `LangMem`, `Beads`, `agentmemory`, `AIngram`, `ProjectMem`, `SigmaLink`,
`mempalace`, `sqlite-vec`, `model2vec`, `fastembed`.

No project in the category uses the `Prova-` stem. The category's naming
convention clusters around `mem*`/`memory*`, which Provalume deliberately avoids —
see [ADR-0001](../adr/ADR-0001-identity-and-scope.md).

## 5. Reproducing this check

```sh
# Identifier availability
curl -s -o /dev/null -w '%{http_code}\n' https://pypi.org/pypi/provalume/json
curl -s -o /dev/null -w '%{http_code}\n' https://registry.npmjs.org/provalume
curl -s 'https://crates.io/api/v1/crates?q=provalume' -H 'User-Agent: name-check'
gh api users/provalume
gh api 'search/repositories?q=provalume' --jq '.total_count'
gh api 'search/users?q=provalume' --jq '.total_count'

# Domains
for d in provalume.com provalume.dev provalume.io provalume.org provalume.ai; do
  printf '%-16s ' "$d"; host "$d" >/dev/null 2>&1 && echo 'HAS DNS' || echo 'no DNS'
done
whois provalume.com | grep -iE 'Creation Date|Registrar:|Registrant Organization'
curl -sL https://provalume.com | grep -o 'application/ld+json.\{0,200\}'
```

## 6. Decision

Adopt `Provalume` / `provalume` for: the GitHub repository `andyyaro/provalume`,
the PyPI distribution `provalume`, the Python import package `provalume`, the CLI
command `provalume`, the MCP server name `provalume`, the project-local state
directory `.provalume/`, and the default database `.provalume/provalume.db`.

Re-check before any of: registering a trademark, buying a domain other than
`provalume.dev`, or publishing under a second namespace (npm, crates.io).
