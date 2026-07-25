"""Secret redaction, applied before every durable write.

Ordering matters and is not negotiable (threat T11)::

    validate -> size caps -> REDACT -> poisoning scan -> hash -> write

Redaction runs on the *structured* payload before it is serialised, and hashing
happens afterwards, so stored hashes are over what is actually on disk. A
post-hoc pass over stored rows would mean the secret hit the disk first.

Every rule favours recall over precision. A redacted false positive is an
annoyance; a persisted credential is an incident. Users will occasionally see
``[REDACTED]`` where nothing was secret — that is the intended trade, and it is
documented in ``docs/security/PRIVACY_MODEL.md``.

This module is deliberately independent of any orchestrator's redaction
implementation. Provalume's core must not import a host (ADR-0014), so the rules
here are its own and are tested independently.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

REDACTED = "[REDACTED]"


class Rule(NamedTuple):
    """One redaction rule.

    ``family`` groups rules for reporting, so redaction metadata records *which
    kind* of secret was found without recording the secret.
    """

    family: str
    pattern: re.Pattern[str]
    replacement: str


#: Applied in order. Most rules replace the whole match; a few keep non-secret
#: context via capture groups so the surrounding text stays readable.
RULES: tuple[Rule, ...] = (
    # --- Provider-prefixed API keys --------------------------------------
    # sk-ant- first: the general sk- rule would otherwise consume it and
    # report the wrong family.
    Rule("anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{8,}"), REDACTED),
    Rule("openai", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), REDACTED),
    Rule("github", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), REDACTED),
    Rule("github", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), REDACTED),
    Rule("gitlab", re.compile(r"\bglpat-[A-Za-z0-9_-]{10,}"), REDACTED),
    Rule("gitlab", re.compile(r"\bglcbt-[A-Za-z0-9_-]{10,}"), REDACTED),
    Rule("google", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), REDACTED),
    Rule("slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), REDACTED),
    Rule("slack", re.compile(r"\bxapp-[A-Za-z0-9-]{10,}"), REDACTED),
    Rule("npm", re.compile(r"\bnpm_[A-Za-z0-9]{20,}"), REDACTED),
    Rule("pypi", re.compile(r"\bpypi-[A-Za-z0-9_-]{16,}"), REDACTED),
    Rule("aws", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), REDACTED),
    # --- Structured credential fields ------------------------------------
    # Capture the key and the separator so the caller's own casing and spacing
    # survive; only the value is replaced. Rewriting the key name would edit text
    # that was not secret.
    Rule(
        "aws",
        re.compile(r"(?i)\b(aws_secret_access_key\b\s*[=:]\s*)\S+"),
        r"\1" + REDACTED,
    ),
    Rule(
        "aws",
        re.compile(r"(?i)\b(aws_session_token\b\s*[=:]\s*)\S+"),
        r"\1" + REDACTED,
    ),
    Rule(
        "azure",
        re.compile(r"(?i)\b(AccountKey|SharedAccessSignature)\s*=\s*[^;\s\"']+"),
        r"\1=" + REDACTED,
    ),
    Rule("azure", re.compile(r"(?i)([?&]sig=)[^&\s\"']+"), r"\1" + REDACTED),
    Rule(
        "gcp",
        re.compile(r'(?i)(["\']?private_?[kK]ey_?[dD]ata["\']?\s*[=:]\s*)["\']?[A-Za-z0-9+/=_-]{8,}["\']?'),
        r"\1" + REDACTED,
    ),
    Rule("npm", re.compile(r"(?i)(_authToken\s*=\s*)\S+"), r"\1" + REDACTED),
    # --- Tokens with recognisable structure -------------------------------
    Rule(
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),
        REDACTED,
    ),
    Rule(
        "bearer",
        re.compile(r"(?i)\b(authorization)\b[\"']?\s*[=:]\s*[\"']?bearer\s+[^\s\"',}]+"),
        r"\1: Bearer " + REDACTED,
    ),
    # The body is bounded, and it cannot contain a `-----` run. Both are **ReDoS
    # fixes, not tidiness**: `.*?` with DOTALL rescanned to the end of the input
    # from every BEGIN marker, so `"-----BEGIN PRIVATE KEY-----" * n` — an
    # unterminated marker repeated, which is ordinary in a log that echoed a
    # truncated key — was quadratic: 0.1s at 20 KB, 11.2s at 250 KB. Repeating the
    # literal a pattern is looking for is the same adversarial shape the error
    # normaliser was already hardened against. Excluding `-----` makes the body
    # fail immediately at the next marker instead of walking to the bound, and
    # `-(?!----)` keeps the single hyphens of an encrypted key's `DEK-Info:`
    # header matchable.
    Rule(
        "pem",
        re.compile(
            r"-----BEGIN [A-Z ]{0,40}PRIVATE KEY-----"
            r"(?:[A-Za-z0-9+/=\s:,.]|-(?!----)){0,8192}?"
            r"-----END [A-Z ]{0,40}PRIVATE KEY-----"
        ),
        REDACTED,
    ),
    # --- URL userinfo: keep scheme, user, and host; drop the password -----
    Rule(
        "url-userinfo",
        re.compile(r"(://[^/\s:@\"']+:)[^@\s\"']+(@)"),
        r"\1" + REDACTED + r"\2",
    ),
    # --- Generic credential assignments ----------------------------------
    # Key side: any identifier *ending* in a sensitive word, so this covers
    # AWS_SESSION_TOKEN, GITLAB_TOKEN, DATABASE_PASSWORD, camelCase apiKey, and
    # a bare token=.
    # Value side: stops at delimiters so it does not swallow the rest of a line,
    # and a negative lookahead skips the three things that are almost never
    # secrets: template placeholders, absolute paths, and status words. Without
    # that lookahead this rule redacts half of every normal log line.
    #
    # The identifier prefix is bounded to 40 characters. That is a **ReDoS fix,
    # not a tidiness choice**: an unbounded lazy `*?` retried every prefix length
    # at every position, which took 10.5 seconds on 20 KB of adversarial input
    # and would have hung the write path on hostile command output (threat T24).
    # Bounding it costs nothing real — no credential environment variable has a
    # 40-character prefix before the word `token` — and it is ~240x faster.
    Rule(
        "generic",
        re.compile(
            r"""(?ix)
            (?<![A-Za-z0-9_])
            (["']?
             [A-Za-z0-9_.\-]{0,40}?
             (?: password | passwd | secret | api[_-]?key | apikey
               | token | credential | auth[_-]?key | access[_-]?key )
             ["']?
             \s* [=:] \s*
            )
            ["']?
            (?!
                \s
              | \$\{ | \{\{ | < | %                 # template placeholders
              | /                                    # absolute paths
              | (?: true|false|null|none|nil|yes|no
                  | required|missing|unset|empty|set|redacted|hidden
                  | \[REDACTED\] )
                (?![A-Za-z0-9])
            )
            [^\s,;&"'}\]]{4,}
            ["']?
            """
        ),
        r"\1" + REDACTED,
    ),
)

#: Keys whose value is replaced wholesale during structured redaction, regardless
#: of what the value looks like. Catches the case the pattern rules cannot: a
#: credential with no recognisable shape sitting under an obviously-secret key.
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "api_key",
        "apikey",
        "api-key",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "session_token",
        "credential",
        "credentials",
        "private_key",
        "privatekey",
        "client_secret",
        "auth",
        "authorization",
        "aws_secret_access_key",
        "aws_session_token",
    }
)


class RedactionReport(NamedTuple):
    """What redaction did, recorded alongside the record.

    Stored so a reader can tell "clean" apart from "cleaned". Never contains the
    secret itself — only which families fired and how many times.

    The match count is named ``matches`` rather than ``count`` because
    ``NamedTuple`` inherits ``tuple.count``, and shadowing it would replace a
    method with an int.
    """

    applied: bool
    families: tuple[str, ...]
    matches: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "families": list(self.families),
            "count": self.matches,
        }


def redact_text(text: str) -> tuple[str, RedactionReport]:
    """Redact a string, returning the result and what fired."""
    families: list[str] = []
    total = 0
    out = text
    for rule in RULES:
        out, hits = rule.pattern.subn(rule.replacement, out)
        if hits:
            total += hits
            if rule.family not in families:
                families.append(rule.family)
    return out, RedactionReport(bool(total), tuple(families), total)


def _redact_any(value: Any, families: list[str], counter: list[int]) -> Any:
    if isinstance(value, str):
        out, report = redact_text(value)
        if report.applied:
            counter[0] += report.matches
            for family in report.families:
                if family not in families:
                    families.append(family)
        return out
    if isinstance(value, dict):
        out_dict: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and key.strip().lower().lstrip("-_") in SENSITIVE_KEYS:
                # Redact by key, whatever the value's shape. A short opaque
                # password would slip past every pattern rule.
                if item is not None and item != "":
                    counter[0] += 1
                    if "sensitive-key" not in families:
                        families.append("sensitive-key")
                    out_dict[key] = REDACTED
                else:
                    out_dict[key] = item
            else:
                out_dict[key] = _redact_any(item, families, counter)
        return out_dict
    if isinstance(value, list):
        return [_redact_any(item, families, counter) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_any(item, families, counter) for item in value)
    return value


def redact_structured(payload: dict[str, Any]) -> tuple[dict[str, Any], RedactionReport]:
    """Redact a structured payload in place of serialising first.

    Structured redaction catches what text redaction cannot — a secret under a
    sensitive key that has no recognisable format — and it runs before
    canonicalisation so the hash covers redacted content.
    """
    families: list[str] = []
    counter = [0]
    result = _redact_any(payload, families, counter)
    if not isinstance(result, dict):  # pragma: no cover - input is always a dict
        msg = "structured redaction must return a mapping"
        raise TypeError(msg)
    return result, RedactionReport(bool(counter[0]), tuple(families), counter[0])


#: Patterns used by ``provalume audit`` to re-scan *stored* content. Narrower and
#: stricter than RULES: this set must not match ``[REDACTED]`` itself, and a hit
#: here means redaction failed, so false positives would be alarming rather than
#: merely noisy.
AUDIT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{8,}")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("github-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("gitlab-pat", re.compile(r"\bglpat-[A-Za-z0-9_-]{10,}")),
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("aws-key-id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{20,}")),
    ("pypi-token", re.compile(r"\bpypi-[A-Za-z0-9_-]{16,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}")),
    ("pem-private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)


def scan_for_secrets(text: str) -> list[str]:
    """Return the names of credential patterns found in already-stored text.

    A non-empty result means redaction failed and is a hard ``audit`` failure.

    An empty result is **not** proof that no secret is present — it proves no
    *known pattern* matched. A credential with no recognisable shape may survive.
    Stated in ``PRIVACY_MODEL.md`` §3 so a clean audit is not mistaken for
    clearance.
    """
    return [name for name, pattern in AUDIT_PATTERNS if pattern.search(text)]
