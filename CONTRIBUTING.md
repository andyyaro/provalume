# Contributing to Provalume

Apache-2.0. **No CLA.** A DCO sign-off (`git commit -s`) is welcome but optional.

---

## Six changes that require security review

Each of these would re-open the memory-poisoning channel that the architecture
exists to close. If a pull request does any of them, say so in the description and
expect the review to focus there:

1. **Putting a language model in the write path.** Extraction is interpretation,
   and interpreting attacker-controlled text is a poisoning primitive.
2. **Exposing promotion, invalidation, or supersession to MCP.** They are absent
   from the surface, not disabled ([ADR-0012](docs/adr/ADR-0012-mcp-permissions.md)).
3. **Letting a record's payload influence its own trust state.** `source` is
   structural, assigned by the code path.
4. **Cross-project or global promotion without human approval**
   ([ADR-0016](docs/adr/ADR-0016-global-memory-deferral.md)).
5. **Making vectors the authorisation gate** rather than a ranking input.
6. **Serving semantic records as current truth without landed history.**

`tests/security/` asserts these mechanically, so most attempts fail CI before a
human sees them. That is the intent.

## Setup

```sh
git clone https://github.com/andyyaro/provalume
cd provalume
uv venv && uv pip install -e ".[signatures,vectors]"
uv pip install pytest pytest-cov mypy ruff bandit pip-audit
```

## The gates

Everything below must pass. CI runs the same commands.

```sh
uv run ruff check src tests
uv run mypy
uv run bandit -q -c pyproject.toml -r src
uv run pip-audit
uv run pytest tests
uv run provalume eval
uv run python docs/design/contrast_check.py
```

Coverage target: **85% branch-aware for the deterministic core** (schemas, store,
policy, writers, retrieval, interchange, redact, sdk).

**Do not weaken a safety check to make a test pass.** If a security test fails,
the change is wrong until proven otherwise.

## What a good change looks like

**Deterministic.** Same inputs, same outputs, byte for byte. No wall-clock time in
a projection, no dict-order dependence, no randomness in ranking. `provalume
rebuild` must still reproduce projections exactly.

**Documented where it is decided.** A new constant in the scoring formula belongs
in [`docs/reference/RETRIEVAL.md`](docs/reference/RETRIEVAL.md) with its
reasoning, not only in the code. An architectural change needs an ADR before the
implementation.

**Honest about its cost.** Every ADR has a "Consequences" section listing what the
decision makes worse. Follow that pattern — a change with no downside usually
means the downside was not looked for.

**Tested for the failure, not just the success.** The interesting tests here are
the refusals: what must *not* be promoted, what must *not* leak, what must *not*
be presented as truth.

## Adding a memory type or promotion rule

1. Write or amend the ADR first.
2. Add the type to `schemas/memories.py`, including its ceiling, its recency
   half-life, and whether it is a claim type or a record type.
3. Add the promotion rule to `policy/promotion.py` with a **named** rule constant.
   The name is stored in every transition and is what makes the model auditable —
   renaming one makes historical transitions unreadable.
4. Add the writer.
5. Add tests, including the refusal cases.
6. Update [`docs/reference/DATA_MODEL.md`](docs/reference/DATA_MODEL.md).

## Adding a dependency

Three mandatory runtime dependencies: pydantic, typer, rich. Adding a fourth
needs a strong argument and a check for network capability —
`tests/security/test_no_network.py` will fail otherwise. Heavy or optional things
go in an extra.

## Style

Ruff and mypy strict decide formatting and typing; do not argue with them in
review. Beyond that: comments explain *why*, not *what*, and a comment that
restates the code is worse than none. Docstrings on public functions state what
the caller needs to know, including what the function will refuse to do.

## Reporting a vulnerability

See [`SECURITY.md`](SECURITY.md). Please do not open a public issue for a
vulnerability.

## Issues

Bug reports are most useful with: what you recorded, what you queried, what you
expected, what you got, and the output of `provalume audit` and `provalume
doctor`.

Feature requests are most useful when they name the failure they would have
prevented. Several items in [`LIMITATIONS.md`](docs/reference/LIMITATIONS.md) are
known gaps — knowing which ones bite in practice is exactly the data 0.1.0 lacks.
