"""Auditing: proving the database still says what it claims.

``provalume audit`` answers five questions with evidence rather than assurance:

1. Is the event chain intact?
2. Do the projections match the journal?
3. Is the schema valid and are the pragmas as expected?
4. Does any stored content still contain a known credential pattern?
5. Does any memory reference evidence that does not exist?

**This is detection, not prevention.** A local attacker with write access to the
file can edit it and recompute the chain. What audit provides is that they cannot
do so *invisibly* unless they also control everywhere the chain head was recorded.
Stated plainly in ``THREAT_MODEL.md`` §7 rather than left implied.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from provalume import redact
from provalume.interchange.hashing import hash_content
from provalume.schemas.trust import TERMINAL_STATES, TrustState
from provalume.store.db import Database
from provalume.store.journal import Journal
from provalume.store.repository import MemoryRepository


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class Finding:
    check: str
    severity: Severity
    message: str
    detail: str = ""

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.check}: {self.message}"


@dataclass
class AuditReport:
    findings: list[Finding] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    chain_head: str = ""

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        """Whether the audit passed. Warnings do not fail it; errors do."""
        return not self.errors

    def add(self, check: str, severity: Severity, message: str, detail: str = "") -> None:
        self.findings.append(Finding(check, severity, message, detail))

    def summary(self) -> str:
        if self.ok and not self.warnings:
            return f"{len(self.checks_run)} checks passed."
        parts = []
        if self.errors:
            parts.append(f"{len(self.errors)} error(s)")
        if self.warnings:
            parts.append(f"{len(self.warnings)} warning(s)")
        return f"{len(self.checks_run)} checks run: " + ", ".join(parts)


class Auditor:
    """Runs integrity checks over a Provalume database."""

    def __init__(
        self, db: Database, journal: Journal, repository: MemoryRepository
    ) -> None:
        self.db = db
        self.journal = journal
        self.repository = repository

    def run(self, *, project_id: str | None = None, deep: bool = True) -> AuditReport:
        report = AuditReport()
        self._check_pragmas(report)
        self._check_sqlite_integrity(report)
        self._check_schema(report)
        self._check_chain(report)
        self._check_projections(report, project_id=project_id)
        self._check_provenance(report, project_id=project_id)
        if deep:
            self._check_secrets(report, project_id=project_id)
        self._collect_stats(report, project_id=project_id)
        return report

    # -- checks ------------------------------------------------------------

    def _check_pragmas(self, report: AuditReport) -> None:
        report.checks_run.append("pragmas")
        problems = self.db.check_pragmas()
        if problems:
            report.add(
                "pragmas",
                Severity.ERROR,
                "database pragmas are not as expected",
                "; ".join(problems),
            )
        else:
            report.add("pragmas", Severity.INFO, "pragmas are as expected")

    def _check_sqlite_integrity(self, report: AuditReport) -> None:
        report.checks_run.append("sqlite_integrity")
        problems = self.db.integrity_check()
        if problems:
            report.add(
                "sqlite_integrity",
                Severity.ERROR,
                "sqlite integrity_check reported problems",
                "; ".join(problems[:10]),
            )
        else:
            report.add("sqlite_integrity", Severity.INFO, "database structure is sound")

        fk = self.db.foreign_key_check()
        if fk:
            report.add(
                "foreign_keys",
                Severity.ERROR,
                "dangling foreign key references",
                "; ".join(fk[:10]),
            )
        report.checks_run.append("foreign_keys")

    def _check_schema(self, report: AuditReport) -> None:
        report.checks_run.append("schema")
        from provalume.store.migrations import SCHEMA_VERSION

        actual = self.db.schema_version()
        if actual != SCHEMA_VERSION:
            report.add(
                "schema",
                Severity.WARNING,
                f"schema version is {actual}, this build expects {SCHEMA_VERSION}",
            )
        else:
            report.add("schema", Severity.INFO, f"schema version {actual}")

        # The append-only triggers are the enforcement mechanism for the whole
        # journal guarantee. Their absence means a database that looks normal and
        # is silently mutable.
        rows = self.db.query(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name IN "
            "('events_no_update', 'events_no_delete')"
        )
        present = {str(r["name"]) for r in rows}
        missing = {"events_no_update", "events_no_delete"} - present
        if missing:
            report.add(
                "append_only_triggers",
                Severity.ERROR,
                "append-only triggers are missing; the journal is not protected",
                f"missing: {', '.join(sorted(missing))}",
            )
        else:
            report.add(
                "append_only_triggers", Severity.INFO, "append-only triggers present"
            )
        report.checks_run.append("append_only_triggers")

    def _check_chain(self, report: AuditReport) -> None:
        report.checks_run.append("event_chain")
        problems = self.journal.verify_chain()
        head, _seq, count = self.journal.head()
        report.chain_head = head

        if problems:
            report.add(
                "event_chain",
                Severity.ERROR,
                f"event chain verification failed at {len(problems)} point(s)",
                "; ".join(problems[:5]),
            )
        else:
            report.add(
                "event_chain",
                Severity.INFO,
                f"chain intact across {count} event(s); head {head[:19] or '(empty)'}",
            )

    def _check_projections(self, report: AuditReport, *, project_id: str | None) -> None:
        """Verify that stored memories match what their content hashes to.

        A mismatch means a memory row was edited directly — memories are mutable
        (they are projections), so unlike events there is no trigger stopping it.
        The content hash is what makes such an edit visible.
        """
        report.checks_run.append("projection_consistency")
        mismatched: list[str] = []
        checked = 0

        for memory in self.repository.iter_all(project_id=project_id):
            checked += 1
            expected = hash_content(memory.content, memory.text)
            if memory.content_hash and memory.content_hash != expected:
                mismatched.append(memory.memory_id)
                if len(mismatched) >= 20:
                    break

        if mismatched:
            report.add(
                "projection_consistency",
                Severity.ERROR,
                f"{len(mismatched)} memory record(s) do not match their content hash",
                f"first: {', '.join(mismatched[:5])}",
            )
        else:
            report.add(
                "projection_consistency",
                Severity.INFO,
                f"{checked} memory record(s) match their content hashes",
            )

        journal_seq = self.journal.latest_seq()
        projected_seq = self.repository.projection_seq()
        if projected_seq < journal_seq:
            report.add(
                "projection_currency",
                Severity.WARNING,
                f"projections are behind the journal ({projected_seq} of {journal_seq}); "
                "run `provalume rebuild` or record a new event to catch up",
            )
        report.checks_run.append("projection_currency")

    def _check_provenance(self, report: AuditReport, *, project_id: str | None) -> None:
        """Verify that referenced evidence events actually exist.

        A memory citing an event that is not in the journal is the
        forged-provenance signature (threat T15): the record claims evidence that
        cannot be produced.
        """
        report.checks_run.append("provenance")
        broken: list[str] = []
        orphaned_supersessions: list[str] = []
        checked = 0

        for memory in self.repository.iter_all(project_id=project_id):
            checked += 1
            if memory.source_event_ids:
                found = {e.event_id for e in self.journal.get_many(memory.source_event_ids)}
                missing = set(memory.source_event_ids) - found
                if missing:
                    broken.append(f"{memory.memory_id} -> {', '.join(sorted(missing)[:2])}")
            if memory.supersedes_id and self.repository.get(memory.supersedes_id) is None:
                orphaned_supersessions.append(memory.memory_id)
            if len(broken) >= 20:
                break

        if broken:
            report.add(
                "provenance",
                Severity.ERROR,
                f"{len(broken)} memory record(s) cite events absent from the journal",
                "; ".join(broken[:5]),
            )
        else:
            report.add(
                "provenance",
                Severity.INFO,
                f"all evidence references resolve across {checked} record(s)",
            )

        if orphaned_supersessions:
            report.add(
                "supersession_chains",
                Severity.WARNING,
                f"{len(orphaned_supersessions)} record(s) supersede a missing predecessor",
                ", ".join(orphaned_supersessions[:5]),
            )
        report.checks_run.append("supersession_chains")

    def _check_secrets(self, report: AuditReport, *, project_id: str | None) -> None:
        """Re-scan stored content for known credential patterns.

        A hit means redaction failed and is a hard error. A clean result is
        **not** proof of no secrets — it proves no *known pattern* matched. A
        credential with no recognisable shape may survive, which is why
        ``PRIVACY_MODEL.md`` §3 tells users to rotate rather than to trust a clean
        audit.
        """
        report.checks_run.append("credential_scan")
        hits: list[str] = []

        for event in self.journal.iter_all(project_id=project_id):
            found = redact.scan_for_secrets(
                json.dumps(event.payload, separators=(",", ":"))
            )
            if found:
                hits.append(f"event {event.event_id}: {', '.join(found)}")
                if len(hits) >= 20:
                    break

        if len(hits) < 20:
            for memory in self.repository.iter_all(project_id=project_id):
                found = redact.scan_for_secrets(
                    memory.text + json.dumps(memory.content, separators=(",", ":"))
                )
                if found:
                    hits.append(f"memory {memory.memory_id}: {', '.join(found)}")
                    if len(hits) >= 20:
                        break

        if hits:
            report.add(
                "credential_scan",
                Severity.ERROR,
                f"{len(hits)} record(s) contain unredacted credential patterns",
                "; ".join(hits[:5]),
            )
        else:
            report.add(
                "credential_scan",
                Severity.INFO,
                "no known credential patterns found in stored content "
                "(absence of a known pattern is not proof of no secrets)",
            )

    def _collect_stats(self, report: AuditReport, *, project_id: str | None) -> None:
        report.stats["events"] = self.journal.count(project_id=project_id)
        report.stats["chain_head"] = report.chain_head

        rows = self.db.query(
            "SELECT trust_state, COUNT(*) AS n FROM memories GROUP BY trust_state"
        )
        report.stats["memories_by_trust"] = {
            str(r["trust_state"]): int(r["n"]) for r in rows
        }

        rows = self.db.query(
            "SELECT memory_type, COUNT(*) AS n FROM memories GROUP BY memory_type"
        )
        report.stats["memories_by_type"] = {
            str(r["memory_type"]): int(r["n"]) for r in rows
        }

        report.stats["transitions"] = int(
            self.db.scalar("SELECT COUNT(*) FROM memory_transitions") or 0
        )
        report.stats["refused_transitions"] = int(
            self.db.scalar("SELECT COUNT(*) FROM memory_transitions WHERE allowed = 0") or 0
        )
        report.stats["unresolved_contradictions"] = int(
            self.db.scalar("SELECT COUNT(*) FROM contradictions WHERE resolved = 0") or 0
        )

        quarantined = report.stats["memories_by_trust"].get(TrustState.QUARANTINED.value, 0)
        if quarantined:
            report.add(
                "quarantine",
                Severity.INFO,
                f"{quarantined} record(s) are quarantined and will not be served as fact",
            )
        terminal = sum(
            report.stats["memories_by_trust"].get(s.value, 0) for s in TERMINAL_STATES
        )
        if terminal:
            report.add(
                "terminal_records",
                Severity.INFO,
                f"{terminal} record(s) are withdrawn (invalidated, superseded, or rejected)",
            )
