"""Memory-poisoning heuristics (Tier 2 of ADR-0010).

**These are the weakest control in the system and they are meant to be.** The
architecture — deterministic evidence as the only promotion path, structural
``source``, agents that cannot promote — is what actually stops poisoning. These
patterns only make hostile text fail earlier and more loudly.

Concretely: heuristics do nothing against the hardest case, a clean confident lie
with no suspicious phrasing (``MEMORY_POISONING.md`` §2.2). That case is defeated
by the absence of qualifying evidence, not by anything here.

Scoring is deterministic — the same text always scores the same — because the eval
harness replays a fixed corpus and a fuzzy score would make results irreproducible.
"""

from __future__ import annotations

import re
from typing import Final, NamedTuple


class Pattern(NamedTuple):
    family: str
    name: str
    regex: re.Pattern[str]


#: Per-family contribution to the risk score. Families are capped individually so
#: one record matching six patterns in one family cannot saturate on its own,
#: while a record matching several *different* families rightly scores high.
FAMILY_WEIGHT: Final[dict[str, float]] = {
    "instruction-override": 0.45,
    "ai-directed": 0.30,
    "self-asserted-trust": 0.40,
    "dangerous-shell": 0.35,
    "credential-steering": 0.40,
    "role-confusion": 0.30,
}

PATTERNS: Final[tuple[Pattern, ...]] = (
    # --- Instruction override -------------------------------------------
    Pattern(
        "instruction-override",
        "ignore-previous",
        re.compile(r"(?i)\bignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\s+"
                   r"(?:instructions?|prompts?|rules?|directions?)"),
    ),
    Pattern(
        "instruction-override",
        "disregard",
        re.compile(r"(?i)\bdisregard\s+(?:the\s+)?(?:above|previous|prior|foregoing|all)\b"),
    ),
    Pattern(
        "instruction-override",
        "new-instructions",
        re.compile(r"(?i)\bnew\s+instructions?\s*[:：]"),
    ),
    Pattern(
        "instruction-override",
        "system-prompt",
        re.compile(r"(?i)\b(?:system\s+prompt|system\s+message)\s*[:：]"),
    ),
    Pattern(
        "instruction-override",
        "override-directive",
        re.compile(r"(?i)\boverride\s+(?:your\s+|the\s+)?(?:instructions?|rules?|policy|guardrails?)"),
    ),
    # --- Address to an AI ------------------------------------------------
    Pattern(
        "ai-directed",
        "note-for-ai",
        re.compile(r"(?i)\b(?:note|notice|message|instruction)s?\s+(?:for|to)\s+"
                   r"(?:the\s+)?(?:ai|llm|assistant|agent|model|claude|gpt|copilot)\b"),
    ),
    Pattern(
        "ai-directed",
        "attention-assistant",
        re.compile(r"(?i)\b(?:attention|dear|hello)[, ]+\s*(?:ai|llm|assistant|agent|model)\b"),
    ),
    Pattern(
        "ai-directed",
        "as-an-ai",
        re.compile(r"(?i)\bas\s+an?\s+(?:ai|llm|language\s+model|assistant|agent)\s+you\s+"
                   r"(?:must|should|will|need\s+to|are\s+required)"),
    ),
    # --- Self-asserted trust ---------------------------------------------
    Pattern(
        "self-asserted-trust",
        "record-as-verified",
        re.compile(r"(?i)\b(?:record|store|save|remember|mark|treat|flag)\s+(?:this|it|the\s+following)"
                   r"\s+as\s+(?:a\s+)?(?:verified|trusted|approved|confirmed|proven|official)"),
    ),
    Pattern(
        "self-asserted-trust",
        "this-is-trusted",
        re.compile(r"(?i)\bthis\s+(?:is|has\s+been)\s+(?:already\s+)?"
                   r"(?:verified|trusted|approved|confirmed|validated|proven)\b"),
    ),
    Pattern(
        "self-asserted-trust",
        "high-confidence-claim",
        re.compile(r"(?i)\b(?:confidence|certainty)\s*[:=]\s*(?:high|1\.0|100%|max)"),
    ),
    Pattern(
        "self-asserted-trust",
        "promote-directive",
        re.compile(r"(?i)\b(?:promote|escalate|elevate)\s+(?:this|it)\s+to\s+"
                   r"(?:verified|reviewed|integrated|trusted|project\s+truth)"),
    ),
    # --- Dangerous shell -------------------------------------------------
    Pattern(
        "dangerous-shell",
        "curl-pipe-shell",
        re.compile(r"(?i)\b(?:curl|wget|fetch)\b[^\n|]{0,200}\|\s*(?:sudo\s+)?(?:ba|z|k|da)?sh\b"),
    ),
    Pattern(
        "dangerous-shell",
        "base64-pipe-shell",
        re.compile(r"(?i)\bbase64\s+(?:-d|--decode)\b[^\n|]{0,120}\|\s*(?:ba|z)?sh\b"),
    ),
    Pattern(
        "dangerous-shell",
        "rm-rf-root",
        re.compile(r"(?i)\brm\s+-[rfRF]{2,}\s+(?:/|~|\$HOME|/\*)(?:\s|$)"),
    ),
    Pattern(
        "dangerous-shell",
        "chmod-777",
        re.compile(r"(?i)\bchmod\s+(?:-R\s+)?0?777\b"),
    ),
    Pattern(
        "dangerous-shell",
        "eval-subshell",
        re.compile(r"(?i)\beval\s+[\"']?\$\("),
    ),
    Pattern(
        "dangerous-shell",
        "history-wipe",
        re.compile(r"(?i)\b(?:history\s+-c|unset\s+HISTFILE|>\s*~/\.bash_history)\b"),
    ),
    # --- Credential steering ---------------------------------------------
    Pattern(
        "credential-steering",
        "commit-secrets",
        re.compile(r"(?i)\b(?:commit|push|add|check\s+in)\b[^\n.]{0,60}\b"
                   r"(?:secrets?|credentials?|\.env|api\s*keys?|private\s+keys?)\b"),
    ),
    Pattern(
        "credential-steering",
        "disable-tls",
        re.compile(r"(?i)\b(?:disable|skip|turn\s+off|bypass|ignore)\b[^\n.]{0,40}\b"
                   r"(?:tls|ssl|certificate|cert)\s*(?:verification|validation|checks?)?\b"),
    ),
    Pattern(
        "credential-steering",
        "insecure-flags",
        re.compile(r"(?i)(?:--insecure\b|--no-verify-ssl\b|verify\s*=\s*False\b|"
                   r"NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*0)"),
    ),
    Pattern(
        "credential-steering",
        "exfiltrate",
        re.compile(r"(?i)\b(?:send|post|upload|transmit|exfiltrate)\b[^\n.]{0,50}\b"
                   r"(?:secrets?|credentials?|tokens?|keys?|\.env)\b[^\n.]{0,50}\bto\b"),
    ),
    Pattern(
        "credential-steering",
        "weaken-auth",
        re.compile(r"(?i)\b(?:disable|remove|bypass|skip)\b[^\n.]{0,40}\b"
                   r"(?:authentication|authorization|auth\s+check|permission\s+check)\b"),
    ),
    # --- Role confusion --------------------------------------------------
    Pattern(
        "role-confusion",
        "fake-role-marker",
        re.compile(r"(?i)(?:^|\n)\s*(?:###\s*)?(?:system|assistant|user|tool|function)\s*[:：]\s*\S"),
    ),
    Pattern(
        "role-confusion",
        "chatml-marker",
        re.compile(r"<\|(?:im_start|im_end|system|assistant|user|endoftext)\|>"),
    ),
    Pattern(
        "role-confusion",
        "fake-tool-call",
        re.compile(r"(?i)<(?:tool_call|function_call|antml:invoke|invoke\s+name)\b"),
    ),
    Pattern(
        "role-confusion",
        "fake-provalume-directive",
        re.compile(r"(?i)\bprovalume\s*[:：]\s*(?:promote|trust|verify|approve)"),
    ),
)


class PoisoningAssessment(NamedTuple):
    """The result of scanning one piece of text.

    ``matches`` names the specific patterns that fired so ``explain`` can show
    *why* a record was penalised. An opaque score would be untunable and
    unfalsifiable.
    """

    risk: float
    matches: tuple[str, ...]
    families: tuple[str, ...]

    @property
    def flagged(self) -> bool:
        return self.risk > 0.0

    def describe(self) -> str:
        if not self.flagged:
            return "no poisoning patterns matched"
        return (
            f"risk {self.risk:.2f} from {len(self.matches)} pattern(s) "
            f"in {', '.join(self.families)}"
        )


def assess(text: str) -> PoisoningAssessment:
    """Score text for poisoning risk in ``[0, 1]``.

    Each family contributes its weight once, however many of its patterns match.
    Family weights then sum and clamp to 1.0, so risk rises with the *diversity*
    of hostile signals rather than with repetition — a single phrase repeated
    thirty times is one signal, while three different families is genuinely
    worse.
    """
    if not text:
        return PoisoningAssessment(0.0, (), ())

    matched: list[str] = []
    families: list[str] = []
    for pattern in PATTERNS:
        if pattern.regex.search(text):
            matched.append(f"{pattern.family}/{pattern.name}")
            if pattern.family not in families:
                families.append(pattern.family)

    if not matched:
        return PoisoningAssessment(0.0, (), ())

    risk = min(1.0, sum(FAMILY_WEIGHT[f] for f in families))
    return PoisoningAssessment(round(risk, 4), tuple(matched), tuple(families))


def assess_structured(payload: object) -> PoisoningAssessment:
    """Scan every string in a nested structure.

    Scanning the serialised form would let an attacker hide a phrase across a
    JSON boundary; scanning each string value catches it wherever it sits.
    """
    parts: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            for key, item in value.items():
                if isinstance(key, str):
                    parts.append(key)
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(payload)
    return assess("\n".join(parts))


def exceeds_threshold(assessment: PoisoningAssessment, threshold: float) -> bool:
    """Whether this must be forced to ``quarantined`` and barred from promotion."""
    return assessment.risk >= threshold
