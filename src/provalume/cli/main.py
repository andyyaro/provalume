"""The ``provalume`` command-line interface.

Designed for progressive disclosure: ``init``, ``doctor``, ``status``, ``recall``,
and ``demo`` are enough to be useful, and the lifecycle commands sit underneath
for when they are needed.

Every command that produces data supports ``--json``, whose shape is a stability
contract (ADR-0017) — integrations parse it, so changing a key is a breaking
change even though no Python signature moved.
"""

from __future__ import annotations

import json as jsonlib
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from provalume import __version__
from provalume.cli.theme import make_console, trust_marker, trust_style, type_style
from provalume.errors import ProvalumeError
from provalume.schemas.memories import MemoryType
from provalume.schemas.trust import Source, TrustState

app = typer.Typer(
    name="provalume",
    help=(
        "Verified, git-aware memory for autonomous software agents.\n\n"
        "Facts your agents proved, not things they said."
    ),
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)

console = make_console()
err_console = make_console(stderr=True)


def _open(
    db: Path | None = None,
    project: str | None = None,
    *,
    use_git: bool = True,
) -> Any:
    from provalume import Provalume

    try:
        return Provalume.open(db, project_id=project, use_git=use_git)
    except ProvalumeError as exc:
        err_console.print(f"[pv.error]error:[/] {exc}")
        raise typer.Exit(code=2) from exc


def _emit(payload: Any, *, as_json: bool) -> bool:
    """Print JSON and report whether output is finished."""
    if as_json:
        console.print_json(jsonlib.dumps(payload, default=str))
        return True
    return False


DbOption = Annotated[
    Path | None,
    typer.Option("--db", help="Database path. Defaults to .provalume/provalume.db"),
]
ProjectOption = Annotated[
    str | None,
    typer.Option("--project", help="Project id. Defaults to the repository identity."),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit JSON.")]


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"provalume {__version__}")
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", "-V", callback=_version_callback, is_eager=True,
                     help="Show the version and exit."),
    ] = False,
) -> None:
    """Verified, git-aware memory for autonomous software agents."""


# --- Setup and health ------------------------------------------------------


@app.command()
def init(
    db: DbOption = None,
    project: ProjectOption = None,
    json: JsonOption = False,
) -> None:
    """Create a Provalume database in this project."""
    pv = _open(db, project)
    status = pv.status()
    if _emit(status, as_json=json):
        return
    console.print(f"[pv.success]Initialized Provalume[/] at [pv.action]{status['database']}[/]")
    console.print(f"  project    {status['project_id']}")
    console.print(f"  schema     v{status['schema_version']}")
    if status["git_available"]:
        console.print(f"  branch     {status['branch']}")
    else:
        console.print("  git        [pv.muted]not available — commit validity is disabled[/]")
    console.print()
    console.print("Next: [pv.action]provalume demo[/] to see it work, "
                  "or [pv.action]provalume doctor[/] to check the environment.")


@app.command()
def doctor(db: DbOption = None, project: ProjectOption = None, json: JsonOption = False) -> None:
    """Check the environment, dependencies, and database health."""
    import sqlite3

    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, *, fatal: bool = False) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail, "fatal": fatal})

    add("python", True, f"{sys.version.split()[0]}")
    add("sqlite", True, f"{sqlite3.sqlite_version}")

    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        conn.close()
        add("fts5", True, "available")
    except sqlite3.Error as exc:
        add("fts5", False, f"NOT available: {exc}", fatal=True)

    try:
        from provalume.retrieval.vectors import HashingEmbedder  # noqa: F401

        add("vectors.baseline", True, "HashingEmbedder available (stdlib, test baseline)")
    except ImportError as exc:  # pragma: no cover
        add("vectors.baseline", False, str(exc))

    try:
        import numpy  # noqa: F401

        add("vectors.numpy", True, "numpy available (fast vector path)")
    except ImportError:
        add("vectors.numpy", True, "numpy absent — pure-Python vector fallback in use")

    try:
        from provalume.interchange.signatures import ed25519_available

        add(
            "signatures.ed25519",
            True,
            "available" if ed25519_available() else "absent — signed imports quarantine",
        )
    except ImportError as exc:  # pragma: no cover
        add("signatures.ed25519", False, str(exc))

    pv = _open(db, project)
    status = pv.status()
    add("database", True, f"{status['database']} (schema v{status['schema_version']})")
    add(
        "git",
        True,
        f"branch {status['branch']}" if status["git_available"]
        else "no repository — commit validity degrades to 'uncertain'",
    )
    pragma_problems = pv.db.check_pragmas()
    add("pragmas", not pragma_problems, "; ".join(pragma_problems) or "as expected")

    report = pv.audit(deep=False)
    add("integrity", report.ok, report.summary(), fatal=not report.ok)

    payload = {"checks": checks, "ok": all(c["ok"] for c in checks)}
    if _emit(payload, as_json=json):
        raise typer.Exit(code=0 if payload["ok"] else 1)

    console.print("[pv.heading]Provalume doctor[/]\n")
    for check in checks:
        mark = "[pv.success]ok[/]  " if check["ok"] else "[pv.error]FAIL[/]"
        console.print(f"  {mark} {check['check']:<20} {check['detail']}")
    console.print()
    if payload["ok"]:
        console.print("[pv.success]All checks passed.[/]")
    else:
        console.print("[pv.error]Some checks failed.[/]")
        raise typer.Exit(code=1)


@app.command()
def status(db: DbOption = None, project: ProjectOption = None, json: JsonOption = False) -> None:
    """Show what this database contains."""
    pv = _open(db, project)
    data = pv.status()
    if _emit(data, as_json=json):
        return

    console.print(f"[pv.heading]{data['project_id']}[/]  [pv.muted]{data['database']}[/]\n")
    console.print(f"  events           {data['events']}")
    console.print(f"  chain head       [pv.provenance]{data['chain_head'][:26] or '(empty)'}[/]")
    console.print(f"  schema           v{data['schema_version']}")
    if data["git_available"]:
        console.print(f"  branch           {data['branch']}")
        console.print(f"  commit           {(data['commit'] or '')[:12]}")

    if data["memories_by_trust"]:
        console.print("\n  [pv.heading]memories by trust[/]")
        for state, count in sorted(data["memories_by_trust"].items()):
            console.print(
                f"    {trust_marker(state)} [{trust_style(state)}]{state:<13}[/] {count}"
            )
    if data["memories_by_type"]:
        console.print("\n  [pv.heading]memories by type[/]")
        for kind, count in sorted(data["memories_by_type"].items()):
            console.print(f"      [{type_style(kind)}]{kind:<13}[/] {count}")


# --- Reading ---------------------------------------------------------------


@app.command()
def recall(
    query: Annotated[str, typer.Argument(help="What to search for.")] = "",
    db: DbOption = None,
    project: ProjectOption = None,
    types: Annotated[
        list[str] | None,
        typer.Option("--type", "-t", help="Memory type filter. Repeatable."),
    ] = None,
    min_trust: Annotated[
        str, typer.Option("--trust", help="Minimum trust state.")
    ] = "observed",
    limit: Annotated[int, typer.Option("--limit", "-n")] = 10,
    branch: Annotated[str | None, typer.Option("--branch")] = None,
    include_terminal: Annotated[
        bool,
        typer.Option(
            "--include-withdrawn",
            help="Include invalidated, superseded, and rejected records.",
        ),
    ] = False,
    explain: Annotated[
        bool, typer.Option("--explain", help="Show why each result matched.")
    ] = False,
    digest: Annotated[
        int | None,
        typer.Option(
            "--digest",
            help="Emit a budgeted digest of this many characters instead.",
        ),
    ] = None,
    json: JsonOption = False,
) -> None:
    """Retrieve memories, ranked and explained."""
    pv = _open(db, project)
    response = pv.recall(
        query,
        memory_types=[MemoryType(t) for t in (types or [])],
        min_trust=TrustState(min_trust),
        include_terminal=include_terminal,
        limit=limit,
        branch=branch,
    )

    if digest is not None:
        composed = response.digest(char_budget=digest, include_reasons=explain)
        if _emit(composed.model_dump(), as_json=json):
            return
        console.print(composed.text)
        return

    if _emit([r.model_dump() for r in response.results], as_json=json):
        return

    if not response.results:
        console.print("[pv.muted]No memories matched.[/]")
        return

    for result in response.results:
        state = result.trust_state.value
        console.print(
            f"\n[pv.muted]{result.rank}.[/] {trust_marker(state)} "
            f"[{trust_style(state)}]{state}[/] "
            f"[{type_style(result.memory_type.value)}]{result.memory_type}[/] "
            f"[pv.muted]score {result.score:.3f}[/]"
        )
        console.print(f"   {result.text}")
        if result.provenance_summary:
            console.print(f"   [pv.provenance]{result.provenance_summary}[/]")
        if not result.presentable_as_current_truth:
            console.print("   [pv.warning]not established current truth[/]")
        if explain:
            for reason in result.explanation.reasons:
                console.print(f"   [pv.muted]why: {reason}[/]")
            for warning in result.explanation.warnings:
                console.print(f"   [pv.warning]warning: {warning}[/]")


@app.command(name="explain")
def explain_command(
    memory_id: Annotated[str, typer.Argument(help="The memory to explain.")],
    db: DbOption = None,
    project: ProjectOption = None,
    transitions: Annotated[bool, typer.Option("--transitions",
                           help="Show the full lifecycle history.")] = False,
    json: JsonOption = False,
) -> None:
    """Show the full evidence chain behind one memory."""
    pv = _open(db, project)
    provenance = pv.explain(memory_id)
    if provenance is None:
        err_console.print(f"[pv.error]no such memory:[/] {memory_id}")
        raise typer.Exit(code=1)

    if _emit(provenance.model_dump(), as_json=json):
        return

    memory = pv.memories.get(memory_id)
    console.print(f"[pv.heading]{memory_id}[/]")
    if memory is not None:
        console.print(f"  {memory.text}\n")

    state = provenance.trust_state.value
    console.print(f"  trust          [{trust_style(state)}]{state}[/]")
    console.print(f"  verification   {provenance.verification_state}")
    console.print(f"  review         {provenance.review_state}")
    console.print(f"  integration    {provenance.integration_state}")
    console.print(f"  provenance     [pv.provenance]{provenance.resolution}[/] "
                  f"— {provenance.resolution_detail}")

    for verification in provenance.verifications:
        mark = "passed" if verification.passed else "FAILED"
        console.print(f"\n  [pv.heading]verification {mark}[/]  `{verification.command}`")
        console.print(f"    event {verification.event_id} ({verification.source})")
        if verification.excerpt:
            console.print(f"    [pv.muted]{verification.excerpt[:200]}[/]")

    for review in provenance.reviews:
        independent = "independent" if review.independent else "NOT independent"
        console.print(f"\n  [pv.heading]review {review.verdict}[/] by {review.reviewer} "
                      f"({independent})")

    for integration in provenance.integrations:
        console.print(f"\n  [pv.heading]integration {integration.state}[/] "
                      f"{integration.commit_sha[:12]} — {integration.resolution}")

    for decision in provenance.decisions:
        console.print(f"\n  [pv.heading]decision[/] {decision.selected}")
        if decision.rejected:
            console.print(f"    rejected: {', '.join(decision.rejected)}")
        if decision.rationale:
            console.print(f"    rationale: {decision.rationale}")

    if transitions and provenance.transitions:
        console.print("\n  [pv.heading]lifecycle[/]")
        for transition in provenance.transitions:
            mark = "[pv.success]ok[/]" if transition["allowed"] else "[pv.warning]refused[/]"
            console.print(
                f"    {mark} {transition['from_state']} -> {transition['to_state']}  "
                f"[pv.lineage]{transition['policy_rule']}[/]"
            )
            if transition["note"]:
                console.print(f"       [pv.muted]{transition['note'][:120]}[/]")


@app.command()
def preflight(
    command: Annotated[
        str, typer.Option("--command", "-c", help="The command about to run.")
    ] = "",
    subsystem: Annotated[
        str, typer.Option("--subsystem", help="Subsystem about to change.")
    ] = "",
    files: Annotated[
        list[str] | None, typer.Option("--file", help="Files about to change.")
    ] = None,
    db: DbOption = None,
    project: ProjectOption = None,
    json: JsonOption = False,
) -> None:
    """Check whether a proposed action already failed.

    Exits 0 whether or not a warning was found. Provalume warns; it does not
    block, and an exit code that meant "blocked" would make it a gate that
    scripts route around.
    """
    pv = _open(db, project)
    result = pv.preflight(command=command, subsystem=subsystem, files=tuple(files or []))

    if _emit(result.model_dump(), as_json=json):
        return
    if not result.matched:
        console.print("[pv.success]No prior failure matches this action.[/]")
        return
    console.print(f"[pv.warning]{result.summary}[/]")


@app.command()
def events(
    db: DbOption = None,
    project: ProjectOption = None,
    source: Annotated[
        str | None, typer.Option("--source", help="Filter by source.")
    ] = None,
    event_type: Annotated[
        str | None, typer.Option("--type", help="Filter by event type.")
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 20,
    json: JsonOption = False,
) -> None:
    """List journal events."""
    from provalume.schemas.events import EventFilter, EventType

    pv = _open(db, project)
    spec = EventFilter(
        project_id=pv.project_id,
        sources=(Source(source),) if source else (),
        event_types=(EventType(event_type),) if event_type else (),
        limit=limit,
        ascending=False,
    )
    found = pv.events(spec)
    if _emit([e.model_dump() for e in found], as_json=json):
        return
    for event in found:
        console.print(
            f"[pv.muted]{event.recorded_at}[/] "
            f"[pv.action]{event.event_type:<26}[/] "
            f"[pv.muted]{event.source:<8}[/] {event.event_id}"
        )


@app.command()
def memories(
    db: DbOption = None,
    project: ProjectOption = None,
    trust: Annotated[
        str | None, typer.Option("--trust", help="Exact trust state.")
    ] = None,
    types: Annotated[list[str] | None, typer.Option("--type", "-t")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 25,
    explain: Annotated[bool, typer.Option("--explain")] = False,
    json: JsonOption = False,
) -> None:
    """List memory records."""
    from provalume.schemas.memories import MemoryFilter

    pv = _open(db, project)
    spec = MemoryFilter(
        project_id=pv.project_id,
        trust_states=(TrustState(trust),) if trust else (),
        memory_types=tuple(MemoryType(t) for t in (types or [])),
        include_terminal=True,
        current_only=False,
        limit=limit,
    )
    found = pv.memory_records(spec)
    if _emit([m.model_dump() for m in found], as_json=json):
        return
    for memory in found:
        state = memory.trust_state.value
        console.print(
            f"{trust_marker(state)} [{trust_style(state)}]{state:<13}[/] "
            f"[{type_style(memory.memory_type.value)}]{memory.memory_type:<12}[/] "
            f"{memory.text[:70]}"
        )
        if explain:
            console.print(f"    [pv.muted]{memory.memory_id}  {memory.scope.describe()}[/]")


# --- Lifecycle -------------------------------------------------------------


@app.command()
def propose(
    text: Annotated[str, typer.Argument(help="The proposed memory text.")],
    memory_type: Annotated[str, typer.Option("--type", "-t")] = "semantic",
    agent: Annotated[str, typer.Option("--agent", help="Proposing agent profile.")] = "",
    db: DbOption = None,
    project: ProjectOption = None,
    json: JsonOption = False,
) -> None:
    """Submit an agent-proposed memory. Lands quarantined, always."""
    pv = _open(db, project)
    event = pv.propose(text=text, memory_type=MemoryType(memory_type), agent=agent)
    if _emit({"event_id": event.event_id, "trust_state": "quarantined"}, as_json=json):
        return
    console.print(f"[pv.success]Proposed[/] as event {event.event_id}")
    console.print("  [pv.muted]Landed quarantined. Promotion requires deterministic "
                  "evidence recorded elsewhere.[/]")


@app.command()
def promote(
    memory_id: Annotated[str, typer.Argument()],
    to: Annotated[str, typer.Option("--to", help="Target trust state.")],
    actor: Annotated[
        str, typer.Option("--actor", help="Who is promoting.")
    ] = "cli-operator",
    note: Annotated[str, typer.Option("--note")] = "",
    db: DbOption = None,
    project: ProjectOption = None,
    json: JsonOption = False,
) -> None:
    """Promote a memory one rung, if its evidence allows."""
    from provalume.errors import TrustError

    pv = _open(db, project)
    try:
        memory = pv.promote(memory_id, TrustState(to), actor=actor,
                            actor_source=Source.HUMAN, note=note)
    except TrustError as exc:
        if json:
            console.print_json(jsonlib.dumps({"ok": False, "rule": exc.rule,
                                              "reason": str(exc)}))
        else:
            err_console.print(f"[pv.error]refused:[/] {exc}")
            err_console.print(f"  [pv.muted]rule: {exc.rule} (the refusal was recorded)[/]")
        raise typer.Exit(code=1) from exc

    if _emit({"ok": True, "memory_id": memory_id, "trust_state": memory.trust_state.value},
             as_json=json):
        return
    console.print(f"[pv.attested]Promoted[/] {memory_id} -> {memory.trust_state}")


@app.command()
def invalidate(
    memory_id: Annotated[str, typer.Argument()],
    reason: Annotated[str, typer.Option("--reason")] = "",
    db: DbOption = None,
    project: ProjectOption = None,
    json: JsonOption = False,
) -> None:
    """Mark a fact as no longer true. History is retained."""
    pv = _open(db, project)
    event = pv.invalidate(memory_id, actor="cli-operator", reason=reason)
    if _emit({"event_id": event.event_id}, as_json=json):
        return
    console.print(f"[pv.lineage]Invalidated[/] {memory_id}")


@app.command()
def supersede(
    old_id: Annotated[str, typer.Argument(help="The record being replaced.")],
    statement: Annotated[
        str, typer.Option("--with", help="The new fact that replaces it.")
    ],
    subject: Annotated[str, typer.Option("--subject")] = "",
    db: DbOption = None,
    project: ProjectOption = None,
    json: JsonOption = False,
) -> None:
    """Replace a fact with a newer one. Both records persist."""
    pv = _open(db, project)
    event = pv.supersede(old_id, statement=statement, subject=subject)
    if _emit({"event_id": event.event_id}, as_json=json):
        return
    console.print(f"[pv.lineage]Superseded[/] {old_id}")


# --- Interchange and maintenance -------------------------------------------


@app.command(name="export")
def export_command(
    directory: Annotated[
        Path, typer.Option("--out", "-o", help="Output directory.")
    ],
    db: DbOption = None,
    project: ProjectOption = None,
    json: JsonOption = False,
) -> None:
    """Export to JSONL. Refuses if audit finds unredacted credentials."""
    pv = _open(db, project)
    try:
        result = pv.export(directory)
    except ProvalumeError as exc:
        err_console.print(f"[pv.error]export refused:[/] {exc}")
        raise typer.Exit(code=1) from exc

    payload = {
        "directory": str(result.directory),
        "events": result.events,
        "memories": result.memories,
        "transitions": result.transitions,
    }
    if _emit(payload, as_json=json):
        return
    console.print(f"[pv.success]Exported[/] to {result.directory}")
    console.print(f"  {result.events} events, {result.memories} memories, "
                  f"{result.transitions} transitions")


@app.command(name="import")
def import_command(
    directory: Annotated[
        Path, typer.Argument(help="Directory holding the JSONL export.")
    ],
    allow_foreign_project: Annotated[bool, typer.Option("--allow-foreign-project")] = False,
    quarantine_unknown: Annotated[bool, typer.Option("--quarantine-unknown")] = False,
    db: DbOption = None,
    project: ProjectOption = None,
    json: JsonOption = False,
) -> None:
    """Import a JSONL export. Imported records are never trusted on arrival."""
    from provalume.interchange.jsonl import summarize

    pv = _open(db, project)
    result = pv.import_records(
        directory,
        allow_foreign_project=allow_foreign_project,
        quarantine_unknown=quarantine_unknown,
    )
    if _emit(
        {
            "accepted": result.accepted,
            "duplicates": result.skipped_duplicates,
            "conflicts": [str(c) for c in result.conflicts],
            "rejected": [str(r) for r in result.rejected],
            "quarantined": [str(q) for q in result.quarantined],
            "ok": result.ok,
        },
        as_json=json,
    ):
        raise typer.Exit(code=0 if result.ok else 1)

    console.print(summarize(result))
    if not result.ok:
        raise typer.Exit(code=1)


@app.command()
def rebuild(
    check: Annotated[
        bool,
        typer.Option("--check", help="Catch up projections without dropping them."),
    ] = False,
    db: DbOption = None,
    project: ProjectOption = None,
    json: JsonOption = False,
) -> None:
    """Rebuild every projection from the event journal."""
    pv = _open(db, project)
    stats = pv.rebuild(check_only=check)
    if _emit(stats.as_dict(), as_json=json):
        return
    console.print(f"[pv.success]Rebuilt[/] from {stats.events_processed} event(s)")
    console.print(f"  memories written {stats.memories_written}, "
                  f"updated {stats.memories_updated}")
    console.print(f"  promotions {stats.promotions}, refusals {stats.refusals}")
    if stats.notes:
        for note in stats.notes[:10]:
            console.print(f"  [pv.muted]{note}[/]")


@app.command()
def audit(
    strict: Annotated[
        bool,
        typer.Option(
            "--strict", help="Exit non-zero on any finding, including warnings."
        ),
    ] = False,
    db: DbOption = None,
    project: ProjectOption = None,
    json: JsonOption = False,
) -> None:
    """Prove chain integrity, projection consistency, pragmas, and redaction."""
    pv = _open(db, project)
    report = pv.audit()

    payload = {
        "ok": report.ok,
        "summary": report.summary(),
        "chain_head": report.chain_head,
        "findings": [
            {"check": f.check, "severity": f.severity.value,
             "message": f.message, "detail": f.detail}
            for f in report.findings
        ],
        "stats": report.stats,
    }
    failed = bool(report.errors) or (strict and bool(report.warnings))

    if _emit(payload, as_json=json):
        raise typer.Exit(code=1 if failed else 0)

    console.print("[pv.heading]Provalume audit[/]\n")
    for finding in report.findings:
        style = {"info": "pv.success", "warning": "pv.warning",
                 "error": "pv.error"}[finding.severity.value]
        console.print(f"  [{style}]{finding.severity.value:<7}[/] "
                      f"{finding.check:<24} {finding.message}")
        if finding.detail:
            console.print(f"          [pv.muted]{finding.detail[:160]}[/]")

    console.print(f"\n  chain head  [pv.provenance]{report.chain_head[:26] or '(empty)'}[/]")
    console.print(f"  {report.summary()}")
    if failed:
        raise typer.Exit(code=1)


# --- Demo, eval, MCP -------------------------------------------------------


@app.command()
def demo(
    html: Annotated[
        Path | None,
        typer.Option("--html", help="Also write a light-themed HTML report here."),
    ] = None,
    keep: Annotated[
        bool,
        typer.Option("--keep", help="Keep the temporary project instead of removing it."),
    ] = False,
) -> None:
    """Run a complete scenario offline in a temporary project."""
    from provalume.demo.scenario import run_demo

    run_demo(console=console, html_out=html, keep=keep)


@app.command(name="eval")
def eval_command(
    scenario: Annotated[
        str | None,
        typer.Option("--scenario", help="Run one scenario by name instead of all."),
    ] = None,
    out: Annotated[
        Path | None, typer.Option("--out", help="Write results JSON here.")
    ] = None,
    json: JsonOption = False,
) -> None:
    """Run the replayable evaluation harness."""
    from provalume.evals.replay import run_all, run_one

    results = run_one(scenario) if scenario else run_all()
    payload = results.as_dict()

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(jsonlib.dumps(payload, indent=2, sort_keys=True) + "\n")

    if _emit(payload, as_json=json):
        raise typer.Exit(code=0 if results.passed else 1)

    console.print("[pv.heading]Provalume evaluation[/]\n")
    for entry in results.scenarios:
        mark = "[pv.success]pass[/]" if entry["passed"] else "[pv.error]FAIL[/]"
        console.print(f"  {mark}  {entry['id']:<3} {entry['name']}")
        if not entry["passed"]:
            for failure in entry["failures"]:
                console.print(f"        [pv.error]{failure}[/]")
    console.print(f"\n  {results.summary()}")
    if not results.passed:
        raise typer.Exit(code=1)


@app.command()
def replay(
    directory: Annotated[
        Path, typer.Argument(help="Directory of a JSONL export to replay.")
    ],
    db: DbOption = None,
    project: ProjectOption = None,
    json: JsonOption = False,
) -> None:
    """Replay an exported journal into a database and rebuild projections."""
    pv = _open(db, project)
    result = pv.import_records(directory)
    stats = pv.rebuild()
    payload = {"imported": result.accepted, "rebuilt_from": stats.events_processed,
               "memories": stats.memories_written}
    if _emit(payload, as_json=json):
        return
    console.print(f"[pv.success]Replayed[/] {result.accepted} record(s); "
                  f"rebuilt {stats.memories_written} memories")


@app.command(name="serve-mcp")
def serve_mcp(
    db: DbOption = None,
    project: ProjectOption = None,
    read_only: Annotated[
        bool,
        typer.Option(
            "--read-only",
            help="Disable all write tools. Recommended for shared environments.",
        ),
    ] = False,
    rate_limit: Annotated[
        int, typer.Option("--rate-limit", help="Maximum tool calls per minute.")
    ] = 60,
) -> None:
    """Serve Provalume over MCP on stdio.

    Read tools plus propose. There is no promotion, invalidation, supersession,
    or delete tool on the MCP surface — not disabled, absent (ADR-0012).
    """
    from provalume.mcp.permissions import PermissionProfile
    from provalume.mcp.server import McpServer

    pv = _open(db, project)
    profile = PermissionProfile.read_only() if read_only else PermissionProfile.default()
    server = McpServer(pv, profile=profile, rate_limit_per_minute=rate_limit)
    server.serve_stdio()


if __name__ == "__main__":  # pragma: no cover
    app()
