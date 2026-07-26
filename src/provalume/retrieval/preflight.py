"""The pre-action warning gate.

Before an agent repeats a risky action, ask whether it already failed. The idea
is ProjectMem's; this is an independent implementation keyed on the deterministic
failure signatures from :mod:`provalume.writers.failures`.

**Default behaviour is to warn, never to block.** Memory must not acquire veto
power over an orchestrator's policy — a memory-poisoning bug would become an
orchestration-control bug, and a false positive would become an outage. Blocking
exists, gated behind an explicit policy, and requires an exact high-confidence
match on a high-risk action.

**Certainty needs the error, not just the command.** The failure signature is
computed over ``(command, error_kind, error_text)``, so a caller that has not run
anything yet cannot reach :data:`CONFIDENCE_EXACT_SIGNATURE`. A command-only
check — which is what ``provalume preflight``, the MCP tool and the Orkestra
adapter issue — tops out at :data:`CONFIDENCE_EXACT_COMMAND` and therefore
always warns. Blocking is reachable only where the caller passes the error it
observed: a retry check, or any caller re-proposing an action it has already
watched fail. That asymmetry is the point. "This exact failure, again" is not
knowable before the failure, and a gate that blocked on "this command, again"
would stop every command that ever failed once.

Every warning is recorded so its usefulness can be measured rather than assumed
(eval scenario 19): whether it was shown, heeded or ignored, and what happened
next.
"""

from __future__ import annotations

import re
from typing import Final

from provalume.schemas.memories import Memory, MemoryFilter, MemoryType
from provalume.schemas.retrieval import PREFLIGHT_BANNER, PreflightMatch, PreflightResult
from provalume.schemas.scope import Applicability
from provalume.schemas.trust import TrustState
from provalume.store.gitinfo import GitInfo, applicability_at
from provalume.store.repository import MemoryRepository
from provalume.writers import failures

#: Confidence assigned by match strength. An exact signature match is the only
#: thing that reaches certainty, because it is the only match that means "this
#: precise failure, again".
CONFIDENCE_EXACT_SIGNATURE: Final = 1.0
CONFIDENCE_EXACT_COMMAND: Final = 0.85
CONFIDENCE_REJECTED_ALTERNATIVE: Final = 0.75
CONFIDENCE_SUBSTANTIAL_OVERLAP: Final = 0.55
CONFIDENCE_FILE_OVERLAP: Final = 0.40

#: A match below this is not worth interrupting for. Tuned to keep the gate quiet:
#: a gate that cries wolf gets ignored, which is worse than no gate at all,
#: because it also teaches agents to dismiss the real warnings.
MIN_REPORT_CONFIDENCE: Final = 0.40

#: Blocking requires all three: an exact signature match, repetition, and an
#: explicitly enabled policy. Any one alone is not enough. Because only a caller
#: that supplies the observed error can reach an exact signature match, a
#: command-only pre-action check never blocks, whatever the policy says.
BLOCK_MIN_CONFIDENCE: Final = 1.0
BLOCK_MIN_OCCURRENCES: Final = 2

#: Overlap matching compares whole words, never raw substrings. ``subsystem="ui"``
#: must not match ``npm run build`` because "build" happens to contain "ui": the
#: reason this tier emits ("previously failed in ui") is a claim with evidence
#: attached, and this is already the noisiest tier.
_WORD: Final = re.compile(r"[a-z0-9]+")

#: A one-character subsystem carries no signal and would match almost anything.
MIN_SUBSYSTEM_CHARS: Final = 2


def _one_line(text: str, limit: int) -> str:
    """Collapse to a single line for the aligned warning layout.

    Failure excerpts are multi-line by nature — a traceback, a test summary — and
    dropping them in verbatim breaks the column alignment that makes the warning
    scannable. The full excerpt remains on the record; this is the summary view.
    """
    collapsed = " ".join(text.split())
    return collapsed[: limit - 1] + "…" if len(collapsed) > limit else collapsed


def _words(text: str) -> list[str]:
    """Lowercase word tokens, split on everything that is not alphanumeric.

    Deliberately coarser than :func:`provalume.store.fts.tokenize`, which keeps
    ``tests/integration`` and ``no:xdist`` whole for the index. Here the opposite
    is wanted: a subsystem named ``tests`` should match a command that ran
    ``tests/integration``, and only as a whole word.
    """
    return _WORD.findall(text.lower())


def _contains_words(haystack: list[str], needle: list[str]) -> bool:
    """Whether ``needle`` appears in ``haystack`` as a contiguous run of words."""
    span = len(needle)
    if not span or span > len(haystack):
        return False
    return any(
        haystack[index : index + span] == needle
        for index, word in enumerate(haystack)
        if word == needle[0]
    )


class PreflightGate:
    """Deterministic pre-action lookup."""

    def __init__(
        self,
        repository: MemoryRepository,
        *,
        git: GitInfo | None = None,
        allow_blocking: bool = False,
    ) -> None:
        self.repository = repository
        self.git = git
        self.allow_blocking = allow_blocking

    def check(
        self,
        *,
        project_id: str,
        command: str = "",
        error_kind: str = "",
        error_text: str = "",
        subsystem: str = "",
        files: tuple[str, ...] = (),
        branch: str | None = None,
        commit_sha: str | None = None,
        limit: int = 5,
    ) -> PreflightResult:
        """Look for prior failures resembling a proposed action.

        ``error_kind`` and ``error_text`` describe an error the caller has
        *already seen* — they are what a retry check supplies, and the only way
        to reach tier 1 and, with it, blocking. Omitting them is the normal
        pre-action case and is not a degraded one: the check still runs, and
        still warns, one tier down.
        """
        matches: list[PreflightMatch] = []
        seen: set[str] = set()

        # 1. Exact signature. The strongest signal available: this precise
        #    failure, previously recorded.
        if command:
            signature = failures.compute(
                command=command, error_kind=error_kind, error_text=error_text
            )
            for row in self.repository.signature_rows(project_id, signature.value):
                memory = self.repository.get(str(row["memory_id"]))
                if memory is None or memory.memory_id in seen:
                    continue
                seen.add(memory.memory_id)
                matches.append(
                    self._build_match(
                        memory,
                        occurrences=int(row["occurrences"]),
                        confidence=CONFIDENCE_EXACT_SIGNATURE,
                        reasons=("exact failure signature match",),
                        commit_sha=commit_sha,
                    )
                )

        # 2. Same command, different error. Still worth saying: the command is
        #    known to be trouble even if it broke differently this time.
        if command:
            normalized = failures.normalize_command(command)
            for row in self.repository.all_signatures(project_id):
                if failures.normalize_command(str(row["command"])) != normalized:
                    continue
                memory = self.repository.get(str(row["memory_id"]))
                if memory is None or memory.memory_id in seen:
                    continue
                seen.add(memory.memory_id)
                matches.append(
                    self._build_match(
                        memory,
                        occurrences=int(row["occurrences"]),
                        confidence=CONFIDENCE_EXACT_COMMAND,
                        reasons=("same command failed previously",),
                        commit_sha=commit_sha,
                    )
                )

        # 3. An alternative a human decision already rejected. Re-proposing it is
        #    not a failure yet, and it is about to be a wasted cycle.
        for memory in self._decisions(project_id):
            from provalume.writers.decisions import rejected_alternatives

            haystack = f"{command} {subsystem}".lower()
            if not haystack.strip():
                continue
            for alternative in rejected_alternatives(memory):
                if alternative.lower() in haystack and memory.memory_id not in seen:
                    seen.add(memory.memory_id)
                    matches.append(
                        self._build_match(
                            memory,
                            occurrences=1,
                            confidence=CONFIDENCE_REJECTED_ALTERNATIVE,
                            reasons=(f"a recorded decision already rejected {alternative!r}",),
                            commit_sha=commit_sha,
                        )
                    )
                    break

        # 4. Subsystem or file overlap with a known gotcha. The weakest tier,
        #    and the one most likely to produce noise, so it is scored lowest.
        if subsystem or files:
            for memory in self._gotchas(project_id, branch):
                if memory.memory_id in seen:
                    continue
                overlap = self._overlap(memory, subsystem=subsystem, files=files)
                if overlap is None:
                    continue
                confidence, reason = overlap
                seen.add(memory.memory_id)
                matches.append(
                    self._build_match(
                        memory,
                        occurrences=int(memory.content.get("occurrences", 1)),
                        confidence=confidence,
                        reasons=(reason,),
                        commit_sha=commit_sha,
                    )
                )

        matches = [m for m in matches if m.confidence >= MIN_REPORT_CONFIDENCE]
        # Deterministic: strongest first, then most-repeated, then by identifier.
        matches.sort(key=lambda m: (-m.confidence, -m.occurrences, m.memory_id))
        matches = matches[:limit]

        should_block = self.allow_blocking and any(
            m.confidence >= BLOCK_MIN_CONFIDENCE
            and m.occurrences >= BLOCK_MIN_OCCURRENCES
            and m.applicability is Applicability.CURRENT
            for m in matches
        )

        return PreflightResult(
            matched=bool(matches),
            should_block=should_block,
            matches=tuple(matches),
            summary=self._summarize(matches, should_block=should_block),
        )

    # -- helpers -----------------------------------------------------------

    def _build_match(
        self,
        memory: Memory,
        *,
        occurrences: int,
        confidence: float,
        reasons: tuple[str, ...],
        commit_sha: str | None,
    ) -> PreflightMatch:
        applicability, applicability_reason = applicability_at(
            record_commit=memory.commit_sha,
            query_commit=commit_sha,
            git=self.git,
            scope_applicability=Applicability.CURRENT,
        )

        resolution = memory.content.get("resolution")
        what_worked = ""
        resolution_commit = ""
        resolved_at = ""
        if isinstance(resolution, dict):
            what_worked = failures.resolution_summary(
                resolution, failed_command=str(memory.content.get("command", ""))
            )
            resolution_commit = str(resolution.get("commit_sha") or "")
            resolved_at = str(resolution.get("recorded_at") or "")

        provenance_bits: list[str] = []
        if memory.attempt_id:
            provenance_bits.append(f"attempt {memory.attempt_id}")
        if memory.author_agent:
            provenance_bits.append(f"agent {memory.author_agent}")
        if memory.scope.branch:
            provenance_bits.append(f"branch {memory.scope.branch}")
        if memory.commit_sha:
            provenance_bits.append(f"commit {memory.commit_sha[:12]}")

        excerpt = str(memory.content.get("excerpt", "")) or str(memory.content.get("finding", ""))

        # A record whose applicability cannot be established still warrants a
        # warning — it just warrants a quieter one. Silently dropping it would
        # make the gate go blind after every rebase.
        adjusted = confidence
        if applicability is Applicability.UNCERTAIN:
            adjusted = confidence * 0.8
        elif applicability is Applicability.HISTORICAL:
            adjusted = confidence * 0.6

        return PreflightMatch(
            memory_id=memory.memory_id,
            failure_signature=str(memory.content.get("failure_signature", "")),
            previous_attempt=str(memory.content.get("command", "")) or memory.text[:200],
            failure_evidence=excerpt[:500] or memory.text[:500],
            what_later_worked=what_worked,
            resolution_commit_sha=resolution_commit,
            resolved_at=resolved_at,
            applicability=applicability,
            provenance="; ".join(provenance_bits) or applicability_reason,
            trust_state=memory.trust_state,
            freshness=memory.freshness,
            occurrences=occurrences,
            match_reasons=reasons,
            confidence=round(min(1.0, adjusted), 4),
        )

    def _gotchas(self, project_id: str, branch: str | None) -> list[Memory]:
        return self.repository.find(
            MemoryFilter(
                project_id=project_id,
                memory_types=(MemoryType.GOTCHA,),
                # Rejected records are exactly what this gate is for: a rejected
                # approach is the clearest possible "do not do this again".
                include_terminal=True,
                current_only=False,
                limit=200,
            )
        )

    def _decisions(self, project_id: str) -> list[Memory]:
        return self.repository.find(
            MemoryFilter(
                project_id=project_id,
                memory_types=(MemoryType.DECISION,),
                min_trust=TrustState.OBSERVED,
                limit=100,
            )
        )

    def _overlap(
        self, memory: Memory, *, subsystem: str, files: tuple[str, ...]
    ) -> tuple[float, str] | None:
        haystack = _words(f"{memory.text} {memory.content.get('command', '')}")

        for path in files:
            name = path.rsplit("/", 1)[-1].lower()
            if name and _contains_words(haystack, _words(name)):
                return CONFIDENCE_FILE_OVERLAP, f"previously failed touching {name}"

        token = subsystem.strip()
        if len(token) >= MIN_SUBSYSTEM_CHARS and _contains_words(haystack, _words(token)):
            return CONFIDENCE_SUBSTANTIAL_OVERLAP, f"previously failed in {subsystem}"
        return None

    def _summarize(
        self, matches: tuple[PreflightMatch, ...] | list[PreflightMatch], *, should_block: bool
    ) -> str:
        """Render the warning.

        The shape is fixed so an agent (or a person) can read it the same way
        every time, and every line is evidence rather than advice. The gate says
        what happened; it does not tell the agent what to do.
        """
        if not matches:
            return ""

        top = matches[0]
        # A resolved failure and an open one need different headlines. Both are
        # worth surfacing — knowing a thing broke once and how it was fixed is
        # useful — but leading a resolved failure with "failed previously" reads
        # as an open trap and invites the agent to avoid an approach that now
        # works. The distinction is the difference between a warning and noise.
        headline = (
            "A similar approach failed previously, and was later resolved."
            if top.what_later_worked
            else "A similar approach failed previously."
        )
        # The banner leads, before any quoted evidence: a reader (or a model)
        # that stops early must still have seen the label.
        lines = [PREFLIGHT_BANNER, "", headline, ""]
        lines.append(f"  Previous attempt   {_one_line(top.previous_attempt, 160)}")
        if top.occurrences > 1:
            times = "twice" if top.occurrences == 2 else f"{top.occurrences} times"
            lines.append(f"  Occurrences        failed {times}")
        if top.failure_evidence:
            lines.append(f"  Failure evidence   {_one_line(top.failure_evidence, 200)}")
        lines.append(f"  What later worked  {top.what_later_worked or '(nothing recorded yet)'}")
        lines.append(f"  Applicability      {top.applicability}")
        lines.append(f"  Provenance         {top.provenance}")
        lines.append(f"  Trust state        {top.trust_state}")
        lines.append(f"  Match confidence   {top.confidence:.2f} ({'; '.join(top.match_reasons)})")

        if len(matches) > 1:
            lines.append("")
            lines.append(f"  ({len(matches) - 1} further related record(s) matched.)")

        if should_block:
            lines.append("")
            lines.append("  BLOCKED by policy: exact repeat of a high-confidence prior failure.")
        else:
            lines.append("")
            lines.append("  This is a warning, not a block. Provalume does not override policy.")
        return "\n".join(lines)
