"""The budgeted digest — working memory, composed at query time.

This is what an agent actually reads, which makes it the live channel from
Provalume into a model with tool access. Two properties are therefore
non-negotiable:

**The banner is always first and always present.** Fixed wording, not a
suggestion — it is the control for threat T4 (instruction replay). It is
mitigation, not prevention: Provalume cannot force a model to honour it.

**The budget is a hard ceiling enforced by construction.** Items are measured
before inclusion — against the footer that will actually be rendered, not a fixed
guess at its size — and the digest is never assembled and then trimmed, because a
post-hoc trim can cut mid-item and leave a memory's claim without its trust
label, or cut the warnings line that says the claim is contested at all. That is
exactly how an untrusted record starts reading like a fact.

The digest is not persisted. Composing it fresh keeps one source of truth and
avoids a second, staler thing to poison.
"""

from __future__ import annotations

from collections.abc import Sequence

from provalume.errors import BudgetExceeded
from provalume.schemas.freshness import FreshnessState
from provalume.schemas.memories import MemoryType
from provalume.schemas.retrieval import (
    CHARS_PER_TOKEN_ESTIMATE,
    DIGEST_BANNER,
    Digest,
    DigestItem,
    RecallResult,
)
from provalume.schemas.scope import Applicability
from provalume.schemas.trust import TrustState

#: Floor for the smallest budget that will be accepted at all, so a truncation
#: notice always fits. The reserve actually applied while admitting items is
#: computed from the footer that will be rendered (see :func:`compose`), because
#: a fixed reserve smaller than the real footer is precisely how a digest ends up
#: assembled and then trimmed — the thing this module promises never to do.
_FOOTER_RESERVE = 160

#: Per-item warnings rolled up to one digest-level line each. Keyed on a stable
#: tag rather than on the rendered sentence: comparing against the sentence is
#: how the same warning ends up printed once per matching record.
_ROLLUP: tuple[tuple[str, str, str], ...] = (
    ("contradicted", "contradiction", "contradictions unresolved among the records below"),
    (
        "could not be determined",
        "applicability",
        "some applicability uncertain at the queried commit",
    ),
    ("provenance", "provenance", "provenance unresolved for some records"),
)

#: Category labels, in the order they appear in a digest. Failures lead
#: deliberately: an agent about to repeat a known mistake needs that first, and a
#: digest that buries it under general facts has failed at its main job.
_SECTION_ORDER: tuple[tuple[MemoryType, str], ...] = (
    (MemoryType.GOTCHA, "Prior failures and lessons"),
    (MemoryType.DECISION, "Decisions already made"),
    (MemoryType.PROCEDURAL, "Verified procedures"),
    (MemoryType.SEMANTIC, "Project facts"),
    (MemoryType.EPISODIC, "What happened recently"),
    (MemoryType.PERFORMANCE, "Agent performance"),
)


def _dedupe_key(result: RecallResult) -> tuple[str, str]:
    """Identity for near-duplicate suppression.

    Keyed on ``(type, text)`` rather than on ``memory_id``: distinct records that
    render identically are duplicates *from the reader's point of view*, which is
    the only view that matters for a digest. Two failures of the same command
    produce two episodic records with the same text and nothing to distinguish
    them once rendered.

    Deliberately exact-match, not fuzzy. A fuzzy comparison could suppress a
    genuinely different record that merely reads similarly, and silently dropping
    real context is worse than occasionally repeating some.
    """
    return (result.memory_type.value, result.text)


def _rollup_tags(result: RecallResult) -> set[str]:
    """Which digest-level rollup lines this result would contribute.

    An upper bound rather than an exact set — every matching needle counts, where
    the emitter stops at the first — because this drives the footer reserve, and
    a reserve may be too generous but never too small.
    """
    return {
        tag
        for warning in result.explanation.warnings
        for needle, tag, _message in _ROLLUP
        if needle in warning
    }


def _omitted_notice(omitted: int, budget: int) -> str:
    return (
        f"\n\n[{omitted} further record(s) matched but did not fit the {budget}-character budget.]"
    )


def _warnings_line(messages: Sequence[str]) -> str:
    return "\n[warnings: " + "; ".join(messages) + "]" if messages else ""


def trust_label(result: RecallResult) -> str:
    """A short, honest label for a record's standing.

    Always text, never colour alone: a digest is read by a model, piped to a
    file, and shown in a terminal that may have no colour at all.
    """
    state = result.trust_state
    if state is TrustState.INTEGRATED:
        return "VERIFIED+LANDED"
    if state is TrustState.REVIEWED:
        return "REVIEWED"
    if state is TrustState.VERIFIED:
        return "VERIFIED"
    if state is TrustState.OBSERVED:
        return "OBSERVED"
    if state is TrustState.QUARANTINED:
        return "QUARANTINED/UNTRUSTED"
    return state.value.upper()


def rendered_label(result: RecallResult) -> str:
    """Both axes in one bracket: trust, then freshness when it is a signal.

    ``current`` is the unmarked state — the axis speaks up only when it has
    something to say (`SUSPECT`, `STALE`, `UNVERIFIABLE`), which also keeps
    the word "current" out of the rendered output entirely, where it would
    collide with the applicability line's vocabulary (ADR-0020). A digest
    line reading ``[VERIFIED]`` for a record whose covering code changed this
    morning is the exact failure the freshness axis exists to eliminate.
    """
    label = trust_label(result)
    if result.freshness is not FreshnessState.CURRENT:
        label += f" · {result.freshness.value.upper()}"
    return label


def render_item(result: RecallResult, *, include_reasons: bool) -> str:
    """Render one digest entry.

    Every entry carries its trust label and provenance inline rather than in a
    legend. A reader — human or model — encountering a claim mid-digest must be
    able to see its standing without scrolling back, because that is precisely
    the moment a poisoned record would otherwise pass as fact.
    """
    lines = [f"- [{rendered_label(result)}] {result.text}"]

    if result.provenance_summary:
        lines.append(f"  evidence: {result.provenance_summary}")

    if not result.presentable_as_current_truth:
        if result.explanation.applicability is Applicability.UNCERTAIN:
            lines.append("  applicability: UNCERTAIN at the queried commit")
        elif result.explanation.applicability is Applicability.HISTORICAL:
            lines.append("  applicability: HISTORICAL — from another line of history")
        elif result.memory_type is MemoryType.SEMANTIC:
            lines.append("  applicability: NOT established project truth (has not landed)")

    if include_reasons and result.explanation.reasons:
        lines.append(f"  why: {'; '.join(result.explanation.reasons[:3])}")

    # Only the warnings a reader must act on inline. Provenance is one of them:
    # a record citing evidence that is not in the journal is the forged-provenance
    # signature (threat T15), and a rollup line at the foot of the digest does not
    # say *which* record it applies to.
    for warning in result.explanation.warnings:
        if warning.startswith(("QUARANTINED", "matched", "provenance")):
            lines.append(f"  warning: {warning}")

    return "\n".join(lines)


def compose(
    results: list[RecallResult],
    *,
    char_budget: int | None = None,
    token_budget: int | None = None,
    include_reasons: bool = False,
    banner: str = DIGEST_BANNER,
) -> Digest:
    """Compose a digest within a hard budget.

    Exactly one of ``char_budget`` or ``token_budget`` should be given. A token
    budget is converted using a documented characters-per-token estimate — an
    estimate, because the true ratio is model-specific and Provalume will not add
    a tokenizer dependency to approximate a number it cannot make exact. Callers
    needing precision should pass a character budget.

    Raises :class:`BudgetExceeded` only when the budget cannot hold the banner
    itself. Ordinary overflow is not an error: items are omitted and the count is
    reported, because a caller with a small budget wants the most important
    context, not a failure.
    """
    # Resolved in one expression rather than narrowed by an assert: an assert
    # disappears under `python -O`, and a budget that silently became None would
    # turn a hard ceiling into an unbounded digest.
    if char_budget is not None:
        budget = char_budget
    elif token_budget is not None:
        budget = token_budget * CHARS_PER_TOKEN_ESTIMATE
    else:
        budget = 4_000

    banner_block = banner + "\n"
    minimum = len(banner_block) + _FOOTER_RESERVE
    if budget < minimum:
        msg = (
            f"character budget {budget} cannot hold the mandatory untrusted-data "
            f"banner and a truncation notice ({minimum} required). The banner is not "
            "optional: it is the control that keeps retrieved memory from reading as "
            "instruction."
        )
        raise BudgetExceeded(msg)

    grouped: dict[MemoryType, list[RecallResult]] = {}
    for result in results:
        grouped.setdefault(result.memory_type, []).append(result)

    used = len(banner_block)
    body_parts: list[str] = []
    items: list[DigestItem] = []
    included_ids: set[str] = set()
    seen_fingerprints: set[tuple[str, str]] = set()
    warnings: list[str] = []
    duplicates = 0

    # The reserve is the footer that could actually be rendered, not a guess: the
    # omission notice at its longest, plus the rollup lines the items admitted so
    # far have already earned. A fixed number smaller than the real footer turns
    # "measured before inclusion" back into a post-hoc trim, and the first thing a
    # post-hoc trim cuts is the warnings line.
    notice_reserve = len(_omitted_notice(len(results), budget))
    pending_tags: set[str] = set()

    for memory_type, heading in _SECTION_ORDER:
        section = grouped.get(memory_type)
        if not section:
            continue

        header = f"\n\n## {heading}\n\n"
        rendered: list[str] = []
        section_chars = 0

        for result in section:
            # Near-duplicate suppression. A failure that recurred produces one
            # gotcha (folded, with an occurrence count) but several episodic
            # records that read identically. Spending budget on the second and
            # third copy displaces context the caller has not seen at all.
            fingerprint = _dedupe_key(result)
            if fingerprint in seen_fingerprints:
                duplicates += 1
                continue

            block = render_item(result, include_reasons=include_reasons)
            cost = len(block) + 2
            tags = pending_tags | _rollup_tags(result)
            reserve = notice_reserve + len(
                _warnings_line([m for _n, tag, m in _ROLLUP if tag in tags])
            )
            if used + len(header) + section_chars + cost + reserve > budget:
                break
            rendered.append(block)
            pending_tags = tags
            seen_fingerprints.add(fingerprint)
            section_chars += cost
            included_ids.add(result.memory_id)
            items.append(
                DigestItem(
                    memory_id=result.memory_id,
                    memory_type=result.memory_type,
                    text=result.text,
                    trust_label=trust_label(result),
                    freshness=result.freshness.value,
                    provenance=result.provenance_summary,
                    reasons=result.explanation.reasons,
                    warnings=result.explanation.warnings,
                    chars=cost,
                )
            )

        if rendered:
            body_parts.append(header + "\n".join(rendered))
            used += len(header) + section_chars

    # Budget overflow only. A record suppressed as a near-duplicate did not
    # *fail to fit*, and counting it as though it had tells the caller their
    # budget is binding when raising it would change nothing.
    omitted = len(results) - len(included_ids) - duplicates

    # Roll the per-item warnings of the admitted items up to one line each.
    seen_tags: set[str] = set()
    for result in results:
        if result.memory_id not in included_ids:
            continue
        for warning in result.explanation.warnings:
            for needle, tag, message in _ROLLUP:
                if needle in warning and tag not in seen_tags:
                    seen_tags.add(tag)
                    warnings.append(message)
                    break

    footer = _omitted_notice(omitted, budget) if omitted > 0 else ""
    footer += _warnings_line(warnings)

    text = banner_block + "".join(body_parts) + footer
    if len(text) > budget:  # pragma: no cover - the computed reserve prevents this
        text = text[:budget]

    return Digest(
        banner=banner,
        items=tuple(items),
        text=text,
        char_budget=budget,
        chars_used=len(text),
        omitted_count=omitted,
        suppressed_duplicates=duplicates,
        warnings=tuple(warnings),
        provenance_refs=tuple(i.memory_id for i in items),
        explanations=tuple(r.explanation for r in results if r.memory_id in included_ids),
    )


def estimate_tokens(text: str) -> int:
    """Estimated token count. An estimate, and labelled as one everywhere."""
    return (len(text) + CHARS_PER_TOKEN_ESTIMATE - 1) // CHARS_PER_TOKEN_ESTIMATE
