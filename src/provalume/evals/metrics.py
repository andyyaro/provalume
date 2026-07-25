"""Evaluation metrics.

Every metric here is computed from a real run of the real engine against
committed fixtures. Nothing is estimated, and nothing is compared against another
project — the harness compares Provalume against Provalume, on Provalume's own
scenarios, and the reports say so (``docs/reference/BENCHMARKS.md``).

Rates are always reported with their denominators. "80% recall precision" over
five results and over five hundred are different claims, and a reader deciding
whether to trust a number needs to see which one it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Counter:
    """A rate with its denominator kept."""

    numerator: int = 0
    denominator: int = 0

    def observe(self, hit: bool) -> None:
        self.denominator += 1
        if hit:
            self.numerator += 1

    @property
    def rate(self) -> float:
        return self.numerator / self.denominator if self.denominator else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "numerator": self.numerator,
            "denominator": self.denominator,
            "rate": round(self.rate, 4),
        }

    def __str__(self) -> str:
        if not self.denominator:
            return "n/a (0 observations)"
        return f"{self.rate:.0%} ({self.numerator}/{self.denominator})"


@dataclass
class Timing:
    """Latency samples in milliseconds.

    Reports the median and the 95th percentile rather than the mean: retrieval
    latency is long-tailed, and a mean hides exactly the slow queries a user
    would notice.
    """

    samples: list[float] = field(default_factory=list)

    def observe(self, milliseconds: float) -> None:
        self.samples.append(milliseconds)

    @property
    def median_ms(self) -> float:
        if not self.samples:
            return 0.0
        ordered = sorted(self.samples)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[mid]
        return (ordered[mid - 1] + ordered[mid]) / 2

    @property
    def p95_ms(self) -> float:
        if not self.samples:
            return 0.0
        ordered = sorted(self.samples)
        index = min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))
        return ordered[index]

    @property
    def max_ms(self) -> float:
        return max(self.samples) if self.samples else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "count": len(self.samples),
            "median_ms": round(self.median_ms, 3),
            "p95_ms": round(self.p95_ms, 3),
            "max_ms": round(self.max_ms, 3),
        }


@dataclass
class Metrics:
    """The metric set the harness reports.

    Named to match ``docs/reference/BENCHMARKS.md``. Metrics whose scenarios did
    not run report a zero denominator rather than a fabricated zero rate, so an
    unrun measurement is visibly unrun rather than looking like a perfect score.
    """

    repeated_error: Counter = field(default_factory=Counter)
    """Fraction of repeat attempts that proceeded without a warning. Lower is
    better; a miss means the gate stayed silent when it should have spoken."""

    task_completion: Counter = field(default_factory=Counter)
    recall_precision: Counter = field(default_factory=Counter)
    """Fraction of returned results that were relevant."""

    recall_coverage: Counter = field(default_factory=Counter)
    """Fraction of relevant records that were returned."""

    stale_memory: Counter = field(default_factory=Counter)
    """Fraction of results presented as current truth that were actually stale.
    Target zero."""

    cross_scope_leakage: Counter = field(default_factory=Counter)
    """Fraction of results from a scope that should have been excluded.
    Target zero — this is threat T9."""

    poisoning_success: Counter = field(default_factory=Counter)
    """Fraction of adversarial records reaching a trust state above `observed`,
    or appearing unlabelled. **Target zero. A non-zero result is a bug, not a
    tuning parameter.**"""

    false_warnings: Counter = field(default_factory=Counter)
    """Fraction of warnings that did not correspond to a genuine prior failure."""

    procedure_reuse: Counter = field(default_factory=Counter)
    verification_improvement: Counter = field(default_factory=Counter)
    review_cycle_reduction: Counter = field(default_factory=Counter)

    retrieval_latency: Timing = field(default_factory=Timing)
    write_latency: Timing = field(default_factory=Timing)
    rebuild_latency: Timing = field(default_factory=Timing)

    context_chars: list[int] = field(default_factory=list)
    """Digest sizes. Reported so a reader can see that budgets bind."""

    def observe_context(self, chars: int) -> None:
        self.context_chars.append(chars)

    @property
    def mean_context_chars(self) -> float:
        if not self.context_chars:
            return 0.0
        return sum(self.context_chars) / len(self.context_chars)

    def as_dict(self) -> dict[str, Any]:
        return {
            "repeated_error": self.repeated_error.as_dict(),
            "task_completion": self.task_completion.as_dict(),
            "recall_precision": self.recall_precision.as_dict(),
            "recall_coverage": self.recall_coverage.as_dict(),
            "stale_memory": self.stale_memory.as_dict(),
            "cross_scope_leakage": self.cross_scope_leakage.as_dict(),
            "poisoning_success": self.poisoning_success.as_dict(),
            "false_warnings": self.false_warnings.as_dict(),
            "procedure_reuse": self.procedure_reuse.as_dict(),
            "verification_improvement": self.verification_improvement.as_dict(),
            "review_cycle_reduction": self.review_cycle_reduction.as_dict(),
            "retrieval_latency": self.retrieval_latency.as_dict(),
            "write_latency": self.write_latency.as_dict(),
            "rebuild_latency": self.rebuild_latency.as_dict(),
            "context": {
                "samples": len(self.context_chars),
                "mean_chars": round(self.mean_context_chars, 1),
                "max_chars": max(self.context_chars) if self.context_chars else 0,
            },
        }

    def merge(self, other: Metrics) -> None:
        for name in (
            "repeated_error",
            "task_completion",
            "recall_precision",
            "recall_coverage",
            "stale_memory",
            "cross_scope_leakage",
            "poisoning_success",
            "false_warnings",
            "procedure_reuse",
            "verification_improvement",
            "review_cycle_reduction",
        ):
            mine: Counter = getattr(self, name)
            theirs: Counter = getattr(other, name)
            mine.numerator += theirs.numerator
            mine.denominator += theirs.denominator
        for name in ("retrieval_latency", "write_latency", "rebuild_latency"):
            getattr(self, name).samples.extend(getattr(other, name).samples)
        self.context_chars.extend(other.context_chars)

    def summary_lines(self) -> list[str]:
        """Human-readable summary. Zero-denominator metrics say so."""
        rows = [
            ("repeated-error rate", self.repeated_error, "lower is better"),
            ("recall precision", self.recall_precision, "higher is better"),
            ("recall coverage", self.recall_coverage, "higher is better"),
            ("stale-memory rate", self.stale_memory, "target 0"),
            ("cross-scope leakage", self.cross_scope_leakage, "target 0"),
            ("poisoning success", self.poisoning_success, "target 0"),
            ("false warnings", self.false_warnings, "lower is better"),
            ("procedure reuse", self.procedure_reuse, "higher is better"),
        ]
        lines = [f"  {name:<24} {counter}  ({note})" for name, counter, note in rows]
        lines.append(
            f"  {'retrieval latency':<24} median {self.retrieval_latency.median_ms:.2f}ms, "
            f"p95 {self.retrieval_latency.p95_ms:.2f}ms"
        )
        lines.append(
            f"  {'write latency':<24} median {self.write_latency.median_ms:.2f}ms, "
            f"p95 {self.write_latency.p95_ms:.2f}ms"
        )
        if self.context_chars:
            lines.append(
                f"  {'digest size':<24} mean {self.mean_context_chars:.0f} chars, "
                f"max {max(self.context_chars)}"
            )
        return lines
