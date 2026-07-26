"""Withdrawal: invalidation, supersession, rejection, and contradiction detection.

Facts are never overwritten and never hard-deleted (ADR-0009). Three ways a record
stops being current, and they mean different things:

**Invalidation** — it stopped being true, no replacement asserted.
**Supersession** — a specific newer record replaces it, linked both ways.
**Rejection** — the work was rejected or the claim disproved. Permanent.

Conflating them loses the *reason* a fact changed, which is the part a later
reader needs. "We no longer use pip" and "we use uv now" are different claims.
"""

from __future__ import annotations

import re
from typing import Final, NamedTuple

from provalume.schemas.memories import Memory, MemoryType
from provalume.schemas.trust import TrustState, is_permanent, is_terminal

RULE_INVALIDATE_EVIDENCE: Final = "invalidate.fact_no_longer_holds"
RULE_INVALIDATE_HUMAN: Final = "invalidate.human_authority"
RULE_INVALIDATE_REVERTED: Final = "invalidate.commit_reverted"
RULE_INVALIDATE_VERIFICATION_REGRESSED: Final = "invalidate.verification_regressed"
RULE_SUPERSEDE: Final = "supersede.replaced_by_newer_record"
RULE_REJECT_REVIEW: Final = "reject.review_verdict"
RULE_REJECT_HUMAN: Final = "reject.human_authority"
RULE_REJECT_BRANCH: Final = "reject.branch_abandoned"

REFUSE_ALREADY_TERMINAL: Final = "refuse.already_terminal"
REFUSE_SELF_SUPERSEDE: Final = "refuse.self_supersession"
REFUSE_CYCLE: Final = "refuse.supersession_cycle"
REFUSE_SCOPE_MISMATCH: Final = "refuse.supersession_scope_mismatch"
REFUSE_TYPE_MISMATCH: Final = "refuse.supersession_type_mismatch"

#: Bound on chain traversal, so a corrupt chain cannot hang a query.
MAX_CHAIN_DEPTH: Final = 64


class WithdrawalDecision(NamedTuple):
    allowed: bool
    rule: str
    reason: str

    def __bool__(self) -> bool:
        return self.allowed


def can_invalidate(memory: Memory, *, rule: str) -> WithdrawalDecision:
    """Whether a record may be invalidated."""
    if is_permanent(memory.trust_state):
        return WithdrawalDecision(
            False,
            REFUSE_ALREADY_TERMINAL,
            f"{memory.trust_state} is permanent; there is nothing left to withdraw",
        )
    if memory.trust_state is TrustState.INVALIDATED:
        return WithdrawalDecision(False, REFUSE_ALREADY_TERMINAL, "already invalidated")
    return WithdrawalDecision(True, rule, "record withdrawn; history retained")


def can_supersede(old: Memory, new: Memory) -> WithdrawalDecision:
    """Whether ``new`` may supersede ``old``.

    Chains are linear by construction. Two records claiming the same predecessor
    is a *conflict*, surfaced rather than resolved by recency — the newer record
    may be the poisoned one.
    """
    if old.memory_id == new.memory_id:
        return WithdrawalDecision(False, REFUSE_SELF_SUPERSEDE, "a record cannot supersede itself")
    if is_permanent(old.trust_state):
        return WithdrawalDecision(
            False,
            REFUSE_ALREADY_TERMINAL,
            f"{old.trust_state} is permanent and cannot be superseded",
        )
    if old.memory_type is not new.memory_type:
        return WithdrawalDecision(
            False,
            REFUSE_TYPE_MISMATCH,
            f"cannot supersede a {old.memory_type} record with a {new.memory_type} one",
        )
    if old.scope.project_id != new.scope.project_id:
        return WithdrawalDecision(
            False,
            REFUSE_SCOPE_MISMATCH,
            "supersession cannot cross a project boundary",
        )
    return WithdrawalDecision(
        True, RULE_SUPERSEDE, f"{new.memory_id} replaces {old.memory_id}; both retained"
    )


def can_reject(memory: Memory, *, rule: str) -> WithdrawalDecision:
    if memory.trust_state is TrustState.REJECTED:
        return WithdrawalDecision(False, REFUSE_ALREADY_TERMINAL, "already rejected")
    return WithdrawalDecision(
        True,
        rule,
        "rejected permanently; retained as negative experience and never promotable",
    )


# --- Subject keys and contradiction detection -----------------------------

_WORD = re.compile(r"[a-z0-9]+")

#: Stopwords removed when deriving a subject key. Deliberately tiny: an
#: aggressive list would collapse distinct subjects onto one key and manufacture
#: contradictions between unrelated facts.
_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "to",
        "of",
        "in",
        "on",
        "at",
        "for",
        "with",
        "by",
        "from",
        "as",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "we",
        "our",
        "you",
        "and",
        "or",
        "but",
        "not",
        "no",
    }
)


def subject_key(text: str, *, max_terms: int = 8) -> str:
    """Derive a normalised subject key from a memory's text.

    Two current semantic records in one scope with the same key and differing
    content are a contradiction (ADR-0009), and supersession uses the same key to
    find the fact being replaced.

    Deliberately literal: lowercase, strip punctuation, drop a small stopword
    set, sort and deduplicate the first terms. It will **miss** paraphrased
    contradictions — "we use uv" versus "the package manager is uv" produce
    different keys. That is the intended failure direction. Detecting more would
    mean interpreting text, which means an LLM in the read path (ADR-0007), and a
    *fabricated* contradiction demotes a correct fact, which is worse than a
    missed one.

    Short words are normally dropped as noise, but a subject made *entirely* of
    them — ``ci``, ``pm``, ``db``, ``ui`` — is a real subject, not noise.
    Returning an empty key there would silently switch off supersession and
    contradiction detection for exactly those subjects, so the filter relaxes
    rather than giving up.
    """
    words = [w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS]
    terms = [w for w in words if len(w) > 2] or words
    if not terms:
        return ""
    unique = sorted(set(terms[: max_terms * 3]))[:max_terms]
    return " ".join(unique)


def contradicts(a: Memory, b: Memory) -> bool:
    """Whether two records are a contradiction pair.

    Requires: both semantic, both current, same project, same effective scope,
    same subject key, different content. Anything less specific produces false
    contradictions, and a false contradiction demotes a correct fact.
    """
    if a.memory_id == b.memory_id:
        return False
    if a.memory_type is not MemoryType.SEMANTIC or b.memory_type is not MemoryType.SEMANTIC:
        return False
    if not (a.is_current and b.is_current):
        return False
    if is_terminal(a.trust_state) or is_terminal(b.trust_state):
        return False
    if a.scope.project_id != b.scope.project_id:
        return False
    if a.scope.repository_id != b.scope.repository_id:
        return False
    # Branch-scoped records on *different* branches are not contradicting each
    # other — that is the concurrent-worktree case, which is legitimate and is
    # handled by scope filtering rather than by flagging a conflict.
    if a.scope.branch != b.scope.branch:
        return False
    if not a.subject_key or a.subject_key != b.subject_key:
        return False
    return a.content_hash != b.content_hash


def walk_supersession(
    start_id: str,
    successor_of: dict[str, str],
    *,
    max_depth: int = MAX_CHAIN_DEPTH,
) -> tuple[list[str], bool]:
    """Follow a supersession chain to its head.

    Returns the path walked and whether a cycle or the depth bound was hit.
    Bounded because a corrupt chain must degrade a query, not hang it.
    """
    path = [start_id]
    seen = {start_id}
    current = start_id
    for _ in range(max_depth):
        nxt = successor_of.get(current)
        if nxt is None:
            return path, False
        if nxt in seen:
            return path, True
        path.append(nxt)
        seen.add(nxt)
        current = nxt
    return path, True
