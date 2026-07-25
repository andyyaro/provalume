# Security policy

## Reporting a vulnerability

Report privately through GitHub's **[Security Advisories](https://github.com/andyyaro/provalume/security/advisories/new)**
for this repository. Please do not open a public issue.

Include: what you can do, what an attacker gains, and a reproduction if you have
one. You will get an acknowledgement within a few days.

This is a solo-maintained project. There is no bug bounty and no guaranteed
response time — that is stated so you can calibrate rather than guess.

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | yes |
| < 0.1 | no |

Pre-1.0: security fixes land on the current minor series.

## What is in scope

Provalume's job is to keep unverified claims out of the trusted tiers, keep
secrets out of durable storage, and keep records inside their scope. In scope:

- Promotion of a record without qualifying evidence
- Anything that lets an agent or MCP client grant trust
- Cross-project or cross-branch leakage
- Secrets surviving redaction into storage or export
- Path traversal, SQL injection, FTS injection
- Undetectable tampering with events, memories, or transitions
- Import accepting forged provenance or a bad signature
- Denial of service through unbounded queries or oversized input

## What is out of scope

These are documented limitations, not vulnerabilities
([`LIMITATIONS.md`](docs/reference/LIMITATIONS.md)):

- **A malicious local operator.** Anyone who can write
  `.provalume/provalume.db` can tamper with it. Tampering is *detectable* via
  `provalume audit`, not preventable.
- **Multi-user isolation.** There is no access control. Do not put a Provalume
  database on a shared host and expect isolation between users.
- **A compromised orchestration kernel.** It is trusted to report deterministic
  outcomes; if it lies, Provalume records the lie faithfully.
- **A model choosing to obey text in a digest.** Provalume labels retrieved
  memory as untrusted data; it cannot enforce that a model honours the label.
  See [`MEMORY_POISONING.md`](docs/security/MEMORY_POISONING.md) §2.4.
- **Redaction missing an unrecognisable secret.** A clean `audit` proves no
  *known pattern* matched, not that no secret is present.
- **Confidentiality of JSONL exports.** Plaintext by design; transport is yours.

## Design commitments

These are asserted by tests, so a change that breaks one fails CI:

- No network code. No telemetry, analytics, crash reporting, update checks, or
  accounts.
- No language model in any canonical path.
- Agents cannot promote memory. Ever.
- MCP exposes no promotion, invalidation, supersession, or delete tool.
- Redaction runs before every durable write, and hashing after it.
- Events are append-only, enforced by database triggers.

Full analysis: [`THREAT_MODEL.md`](docs/security/THREAT_MODEL.md) — 26 threats
with their controls, and a residual-risk section that does not pretend.
