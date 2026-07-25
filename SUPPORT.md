# Support

## Start here

| Question | Where |
|---|---|
| How do I use it? | [Quickstart](docs/QUICKSTART.md), or `provalume demo` |
| What does "verified" mean? | [Trust model](docs/security/TRUST_MODEL.md) |
| Why did this rank first? | `provalume explain <memory-id>` |
| Why was this *not* returned? | [Retrieval](docs/reference/RETRIEVAL.md) — the filters run before scoring |
| Why can't my agent promote memory? | [ADR-0012](docs/adr/ADR-0012-mcp-permissions.md). It is deliberate. |
| Is something broken? | `provalume doctor` and `provalume audit` |
| Is this a known gap? | [Limitations](docs/reference/LIMITATIONS.md) |
| Why is it built this way? | [ADRs](docs/adr/) — 18 decisions, each with its cost |

## Diagnosing

```sh
provalume doctor          # environment, FTS5, Git, permissions
provalume audit           # chain, projections, pragmas, redaction, provenance
provalume status          # what this database contains
provalume explain <id> --transitions
```

`audit` is the one to run first. It checks the things that would make everything
else untrustworthy.

## Things that are working as intended

**"My agent's memory is stuck in quarantined."** Correct. Agent-proposed memory
lands quarantined and needs deterministic evidence recorded elsewhere. The party
making a claim is never the party granting it trust.

**"A semantic fact says it is not established truth."** It has not landed in
history. Verification passing in one worktree does not change the project.

**"Applicability says uncertain."** Ancestry could not be determined — usually a
rebase, a cherry-pick, or a missing repository. A labelled uncertainty beats a
confident wrong answer.

**"`[REDACTED]` appeared where nothing was secret."** Redaction favours recall
over precision. A false positive is annoying; a persisted credential is an
incident.

**"The MCP server has no promote tool."** By design, and asserted by a test.

## Reporting a bug

Open an issue with: what you recorded, what you queried, what you expected, what
you got, plus `provalume doctor` and `provalume audit --json` output. Redact
anything sensitive first — the output can contain command strings and paths.

## Reporting a vulnerability

Privately, via [Security Advisories](https://github.com/andyyaro/provalume/security/advisories/new).
Not a public issue. See [`SECURITY.md`](SECURITY.md).

## Expectations

Solo-maintained. Issues are read; responses are best-effort. Security reports get
priority over everything else.
