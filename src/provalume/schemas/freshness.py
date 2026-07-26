"""The freshness axis: does the code still support this record's evidence?

Freshness is deliberately not a trust state (ADR-0020). Trust answers *how
well was this proven, and by whom*; freshness answers *does the code still
support it*. A record can be ``integrated`` and ``stale`` at once — highest
trust, lowest freshness — and that combination is the dangerous case this axis
exists to surface. Nothing here may influence promotion: the freshness event
types are deliberately absent from ``EVIDENCE_EVENT_TYPES``, and no freshness
transition can raise or lower a trust state.

Vocabulary only. The behaviour that moves these states lives elsewhere and
arrives milestone by milestone; this module is the closed set of words it is
allowed to use.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

__all__ = [
    "DEFAULT_FRESHNESS",
    "IRRELEVANT_REASON_CODES",
    "BlastRadiusMethod",
    "FreshnessState",
    "ReasonCode",
    "RelevanceVerdict",
    "ReverificationOutcome",
]


class FreshnessState(StrEnum):
    """Where a record sits on the freshness axis. Exactly one per record."""

    CURRENT = "current"
    """A blast radius is recorded and no landed commit has touched it since the
    record's evidence was produced — or the most recent touch was assessed
    irrelevant, or a re-run passed after it."""

    SUSPECT = "suspect"
    """A landed commit touched the blast radius and the change has not been
    ruled irrelevant or survived a re-run. An invitation to check, not an
    assertion of falsehood."""

    STALE = "stale"
    """A re-execution of the record's own command failed. A machine
    observation with a recorded environment fingerprint — never a judgement,
    and never the same thing as ``TrustState.INVALIDATED``."""

    UNVERIFIABLE = "unverifiable"
    """The machine cannot make a freshness claim: no re-runnable command, an
    environment that no longer resolves, or no recorded blast radius (which
    includes every record written before this axis existed). Honest
    uncertainty, never a default of ``current``."""


#: A record starts here. ``current`` must be earned by a recorded blast
#: radius; claiming it without one would assert "nothing changed underneath
#: this" without the means to know.
DEFAULT_FRESHNESS: Final = FreshnessState.UNVERIFIABLE


class BlastRadiusMethod(StrEnum):
    """How a blast radius was extracted, in descending order of precision.

    The method travels with the radius because the three are very different
    evidence: ``coverage`` observed what actually ran, ``import_graph`` bounds
    what could run, ``commit_touch`` merely names what changed alongside.
    Downstream consumers may weight them; they may not conflate them.
    """

    COVERAGE = "coverage"
    IMPORT_GRAPH = "import_graph"
    COMMIT_TOUCH = "commit_touch"


class RelevanceVerdict(StrEnum):
    """A deterministic differ's answer: could this change affect the outcome?"""

    RELEVANT = "relevant"
    IRRELEVANT = "irrelevant"


class ReasonCode(StrEnum):
    """Why the differ reached its verdict. A closed enum, never free text —
    deterministic output means enumerable reasons."""

    WHITESPACE_ONLY = "whitespace_only"
    COMMENT_ONLY = "comment_only"
    DOCSTRING_ONLY = "docstring_only"
    SIGNATURE_CHANGED = "signature_changed"
    BODY_CHANGED = "body_changed"
    IMPORT_CHANGED = "import_changed"
    UNPARSEABLE = "unparseable"


#: The only reason codes that may short-circuit a trigger and leave a record
#: ``current``. Everything else escalates — including ``unparseable``: a
#: differ that cannot read a file does not get to call the change harmless.
IRRELEVANT_REASON_CODES: Final[frozenset[ReasonCode]] = frozenset(
    {
        ReasonCode.WHITESPACE_ONLY,
        ReasonCode.COMMENT_ONLY,
        ReasonCode.DOCSTRING_ONLY,
    }
)


class ReverificationOutcome(StrEnum):
    """What happened when a stored command was re-executed.

    ``errored`` is the engine failing, not the record failing — fail-open
    means it produces no freshness transition at all (I5).
    """

    PASSED = "passed"
    FAILED = "failed"
    ERRORED = "errored"
