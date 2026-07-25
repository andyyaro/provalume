## What this changes

<!-- And why. If it fixes an issue, link it. -->

## What it costs

<!-- Every ADR in this repository lists what its decision makes worse. Do the
     same here. A change with no downside usually means the downside was not
     looked for. -->

## Gates

- [ ] `ruff check src tests`
- [ ] `mypy`
- [ ] `bandit -q -c pyproject.toml -r src`
- [ ] `pytest tests`
- [ ] `provalume eval`

## Does this touch a standing commitment?

Six architectural commitments hold the security model together
([CONTRIBUTING.md](../blob/main/CONTRIBUTING.md)). Tick any that apply — each
needs an ADR and a security review.

- [ ] Language model in the write path
- [ ] Promotion, invalidation, or supersession on the MCP surface
- [ ] Payload influencing its own trust state
- [ ] Cross-project promotion without human approval
- [ ] Vectors as an authorisation gate
- [ ] Semantic records served as truth without landed history

## Checklist

- [ ] New constants are documented where they are decided, not only in code
- [ ] Refusal cases are tested, not just success cases
- [ ] Determinism preserved — `provalume rebuild` still reproduces projections
- [ ] `CHANGELOG.md` updated if user-visible
