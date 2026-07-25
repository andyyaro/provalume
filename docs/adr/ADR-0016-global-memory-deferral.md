# ADR-0016: Global cross-project memory deferred

**Status:** Accepted · **Date:** 2026-07-25

## Context

Some knowledge is genuinely cross-project: "this machine's Docker daemon needs
`colima start` first", "our team always uses conventional commits", "the corporate
proxy breaks `pip` but not `uv`". A per-project store rediscovers all of it in every
new project.

Against that: **cross-project leakage is threat T9, rated Critical.** Project A's
memory reaching project B does not just produce a wrong answer — it can carry
internal hostnames, customer names in test fixtures, and secrets that redaction
missed, out of the context where the operator expected them to stay.

Provalume also has no production data on which cross-project facts actually recur,
because it ships standalone rather than after a month of dogfooding
([`RESEARCH_VALIDATION.md`](../research/RESEARCH_VALIDATION.md) §1). Designing a
promotion policy for a class of facts nobody has measured is guessing.

## Decision

**Global cross-project memory is not implemented in 0.1.0. The scope value exists in
the model; no code path can reach it.**

Concretely:

- No `~/.provalume/` directory. Provalume creates nothing outside the project.
- Databases are project-local. Two projects cannot see each other's memory because
  they are separate files.
- `project_id` is `NOT NULL` on events and memories, and filtered on **every**
  retrieval path — always, with no bypass flag.
- `global` exists as a scope value so the schema does not need a migration later,
  and **no promotion rule targets it.** Attempting to promote to global raises, and
  `tests/security/test_scope_isolation.py` asserts it.
- Importing records with a foreign `project_id` is rejected unless
  `--allow-foreign-project` is passed, and even then they arrive `source=import`
  with an `observed` ceiling.

### Why the scope value exists at all

Reserving the enum value now means adding global scope later is a policy change, not
a schema migration across every table. The value is inert: present in the type,
unreachable in the code.

### The intended shape when it lands

Recorded so that a future implementation starts from the security reasoning rather
than from convenience:

1. A separate database at `~/.provalume/global.db`, never the project database.
   Physical separation makes leakage a bug that has to cross a file boundary.
2. Promotion to global requires **explicit human approval, per record.** Not a
   config flag, not a batch operation — an operator looking at one record.
3. A stricter redaction pass on promotion, plus a mandatory audit gate.
4. Retrieval from global is opt-in per query, and global results are labelled
   distinctly in digests so their origin is never ambiguous.
5. No project identifiers, worktree paths, branch names, or commit SHAs cross the
   boundary — a global fact is about the *machine* or the *team*, never about
   another project.

### What replaces it in 0.1.0

The JSONL interchange ([ADR-0011](ADR-0011-jsonl-interchange.md)). A user who wants
a fact in another project can export it, review the plaintext diff, and import it
deliberately. Manual, auditable, and impossible to do by accident — which for a
Critical-severity leakage path is the right ergonomics.

## Consequences

**Good.** The Critical leakage threat is closed by construction rather than by
policy: the capability does not exist. Every new project starts clean, which is also
the correct default for a system whose records carry provenance claims — a fact
proved in project A was not proved in project B.

**Bad.** Real duplicated effort. Machine-level environment gotchas are rediscovered
per project. This is the honest cost and it is the most likely thing users will ask
for first.

**Bad.** JSONL export/import is more friction than a shared store. Deliberate
friction for a Critical path.

**Also bad.** Deferring means the eventual design has to fit an existing schema. The
reserved scope value and the recorded intended shape are the mitigations.

## Alternatives rejected

**Ship global memory in 0.1.0.** The riskiest feature in the system, designed
without data, gated by a policy written in a hurry. The failure mode is a secret
crossing a project boundary — not something to iterate on in public.

**A global store with an allow-list of "safe" fact types.** Requires deciding in
advance which facts are safe to leak, which is the judgement that needs the data
Provalume does not have.

**One database for all projects with `project_id` filtering.** Puts every project's
data one missing `WHERE` clause away from every other project. Separate files make
the mistake impossible rather than merely tested-against.

**Automatic promotion of facts seen in N projects.** Frequency is not safety. A
secret misredacted in three projects is still a secret.
