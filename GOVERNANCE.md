# Governance

## Today

Provalume is maintained by Andy Yaro. Decisions are made by the maintainer, in
the open, and recorded as ADRs when they are architectural.

That is an accurate description of a young project, not an aspiration. If it
grows contributors, this document changes to match.

## How decisions are recorded

Architectural decisions go in [`docs/adr/`](docs/adr/) **before** the
implementation, each with:

- the context that forced it,
- the decision,
- **what it costs** — every ADR has a Consequences section listing what the
  decision makes worse,
- the alternatives rejected, and why.

An ADR is binding until superseded by a later ADR. If the code disagrees with an
accepted ADR, one of them is a bug; an issue should say which.

## Changes that need more than a maintainer's opinion

Six architectural commitments hold the security model together. Changing any
requires an ADR **and** a security review, and each is asserted by a test in
`tests/security/`:

1. No language model in the write path.
2. No promotion, invalidation, or supersession on the MCP surface.
3. Payload never influences its own trust state.
4. No cross-project or global promotion without human approval.
5. Vectors never authorise; they only reorder.
6. Semantic records are never served as current truth without landed history.

Listed in [`CONTRIBUTING.md`](CONTRIBUTING.md) so contributors meet them early.

## Licensing

Apache-2.0. **No CLA** — contributors keep their copyright, and the project
cannot be unilaterally relicensed. That is deliberate: this project reviewed
competitors partly on their CLA and dependency risk, and inheriting the same
problem would be inconsistent.

DCO sign-off (`git commit -s`) is welcome but optional.

## Releases

See [`docs/RELEASE.md`](docs/RELEASE.md). Semantic versioning; pre-1.0 minor
bumps may break the SDK, stated plainly rather than implied. Tags are annotated,
immutable, and never moved or reused. Publication runs through PyPI Trusted
Publishing over OIDC — there is no long-lived API token to leak.

## If this project is abandoned

Everything needed to fork is in the repository: the ADRs explain why it is built
this way, the threat model explains what it defends against, the eval harness
proves the mechanisms work, and the JSONL interchange means no user's data is
trapped in a database only this code can read.

Apache-2.0 with no CLA means nobody needs permission.
