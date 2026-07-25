"""``provalume demo`` — the whole product in under a minute.

Constraints, all of them deliberate: no API key, no agent CLI, no network, a
temporary project that is cleaned up, and **the real storage, policy, retrieval,
and projection code**. A demo that mocked its own engine would prove nothing,
which is the failure mode this file exists to avoid.

The twelve beats:

  1. Agent A attempts a change
  2. Verification fails
  3. A gotcha is recorded
  4. Agent B is warned before repeating it
  5. Agent B uses a better approach
  6. Verification passes
  7. An independent reviewer approves
  8. The commit is integrated
  9. The procedure is promoted
 10. A stale fact is superseded
 11. A future task retrieves the verified memory with provenance
 12. The user sees why each memory was retrieved
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - creates a throwaway demo repository
import tempfile
import time
from pathlib import Path
from typing import Any

from rich.console import Console

DEMO_PROJECT = "provalume-demo"

FAILING_COMMAND = "pytest -n auto tests/integration"
WORKING_COMMAND = "pytest -p no:xdist tests/integration"
FAILURE_EXCERPT = (
    "E   TimeoutError: deadlock in db fixture teardown after 30.5s\n"
    "=== 1 failed, 42 passed in 61.2s ==="
)


def _beat(console: Console, number: int, title: str) -> None:
    console.print(f"\n[pv.muted]{number:>2}.[/] [pv.heading]{title}[/]")


def _init_repo(path: Path) -> str:
    """Create a throwaway Git repository so commit validity is exercised for real."""
    def git(*args: str) -> str:
        return subprocess.run(  # noqa: S603 - fixed argv, throwaway directory  # nosec B603 B607
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()

    # -b main so the branch the demo reports matches the branch it created,
    # whatever the machine's init.defaultBranch happens to be.
    git("init", "-q", "-b", "main")
    git("config", "user.email", "demo@provalume.invalid")
    git("config", "user.name", "Provalume Demo")
    git("config", "commit.gpgsign", "false")
    (path / "README.md").write_text("# demo project\n")
    git("add", "-A")
    git("commit", "-qm", "initial commit")
    return git("rev-parse", "HEAD")


def run_demo(
    *,
    console: Console | None = None,
    html_out: Path | None = None,
    keep: bool = False,
) -> dict[str, Any]:
    """Run the demo. Returns a summary so tests can assert on it."""
    from provalume import Provalume
    from provalume.schemas.trust import TrustState

    out = console or Console()
    started = time.monotonic()
    workdir = Path(tempfile.mkdtemp(prefix="provalume-demo-"))

    try:
        head = _init_repo(workdir)
        pv = Provalume.open(project_id=DEMO_PROJECT, root=workdir)

        out.print("[pv.heading]Provalume demo[/]")
        out.print(f"[pv.muted]temporary project at {workdir}[/]")
        out.print("[pv.muted]no API key, no agent CLI, no network — real engine[/]")

        # 1-2 -----------------------------------------------------------
        _beat(out, 1, "Agent A attempts the integration suite in parallel")
        out.print(f"     $ {FAILING_COMMAND}")

        _beat(out, 2, "Verification fails")
        pv.record_verification(
            command=FAILING_COMMAND,
            passed=False,
            exit_code=1,
            excerpt=FAILURE_EXCERPT,
            error_kind="test_failure",
            purpose="the integration suite",
            agent_profile="agent-A",
            task_id="task-1",
            attempt_id="attempt-1",
        )
        out.print("     [pv.error]exit 1[/] — TimeoutError: deadlock in db fixture teardown")

        # 3 -------------------------------------------------------------
        _beat(out, 3, "A gotcha is recorded, keyed on a deterministic failure signature")
        gotchas = pv.memory_records(memory_types=("gotcha",), limit=5)
        gotcha = gotchas[0]
        out.print(f"     [pv.trust.verified]{gotcha.trust_state}[/] {gotcha.text[:96]}")
        out.print(f"     [pv.muted]signature {gotcha.content['failure_signature'][7:19]} — "
                  f"paths, timings, and PIDs normalised away[/]")

        # A second agent hits the same wall, so the count elevates.
        pv.record_verification(
            command=FAILING_COMMAND,
            passed=False,
            exit_code=1,
            excerpt=FAILURE_EXCERPT.replace("30.5s", "44.1s").replace("61.2s", "72.8s"),
            error_kind="test_failure",
            purpose="the integration suite",
            agent_profile="agent-B",
            task_id="task-2",
            attempt_id="attempt-2",
        )
        out.print("     [pv.muted]agent-B hits it too — folded into one record, "
                  "occurrences now 2[/]")

        # 4 -------------------------------------------------------------
        _beat(out, 4, "Agent B is warned before repeating it")
        warning = pv.preflight(
            command=FAILING_COMMAND,
            error_kind="test_failure",
            error_text=FAILURE_EXCERPT,
        )
        for line in warning.summary.splitlines():
            out.print(f"     [pv.warning]{line}[/]" if line.strip() else "")

        # 5-6 -----------------------------------------------------------
        _beat(out, 5, "Agent B tries a different approach")
        out.print(f"     $ {WORKING_COMMAND}")

        _beat(out, 6, "Verification passes")
        pv.record_verification(
            command=WORKING_COMMAND,
            passed=True,
            exit_code=0,
            purpose="the integration suite",
            agent_profile="agent-B",
            task_id="task-2",
            attempt_id="attempt-3",
        )
        out.print("     [pv.success]exit 0[/] — 43 passed")
        procedures = pv.memory_records(memory_types=("procedural",), limit=5)
        procedure = procedures[0]
        out.print(f"     [pv.trust.verified]{procedure.trust_state}[/] "
                  "procedure recorded, keyed on the exact command")

        # 7 -------------------------------------------------------------
        _beat(out, 7, "An independent reviewer approves")
        pv.record_review(
            reviewer="reviewer-2",
            approved=True,
            subject="integration test configuration",
            agent_profile="reviewer-2",
            task_id="task-2",
            attempt_id="attempt-3",
        )
        out.print("     [pv.muted]reviewer-2 is not the author (agent-B), so the "
                  "review counts as independent[/]")

        # 8-9 -----------------------------------------------------------
        _beat(out, 8, "The commit is integrated")
        pv.record_integration(
            commit_sha=head, target="user", branch="main", task_id="task-2"
        )
        out.print(f"     [pv.muted]landed at {head[:12]}[/]")

        _beat(out, 9, "The procedure is promoted to verified+landed")
        procedure = pv.memories.get(procedure.memory_id) or procedure
        out.print(f"     [pv.attested]{procedure.trust_state}[/]  {procedure.text[:82]}")
        transitions = pv.memories.transitions_for(procedure.memory_id)
        for transition in reversed(transitions):
            if transition["allowed"]:
                out.print(f"       [pv.lineage]{transition['from_state']} -> "
                          f"{transition['to_state']}[/]  {transition['policy_rule']}")

        # 10 ------------------------------------------------------------
        _beat(out, 10, "A stale fact is superseded, not overwritten")
        pv.record_fact(subject="test runner", statement="Integration tests run in parallel.")
        pv.record_fact(
            subject="test runner",
            statement="Integration tests run serially; the db fixture is not parallel-safe.",
            changed=True,
        )
        facts = pv.memory_records(
            memory_types=("semantic",), include_terminal=True, current_only=False, limit=10
        )
        for fact in facts:
            marker = "superseded" if fact.trust_state is TrustState.SUPERSEDED else "current"
            style = "pv.trust.superseded" if marker == "superseded" else "pv.trust.observed"
            out.print(f"     [{style}]{marker:<11}[/] {fact.text[:76]}")
        out.print("     [pv.muted]history survives — the old fact is retained, "
                  "not deleted[/]")

        # 11-12 ---------------------------------------------------------
        _beat(out, 11, "A later task retrieves the verified memory with provenance")
        response = pv.recall("integration tests parallel", limit=4)
        for result in response.results:
            state = result.trust_state.value
            out.print(f"     [pv.trust.{state}]{state:<11}[/] "
                      f"[pv.type.{result.memory_type.value}]{result.memory_type:<11}[/] "
                      f"{result.text[:62]}")
            if result.provenance_summary:
                out.print(f"       [pv.provenance]{result.provenance_summary}[/]")

        _beat(out, 12, "And can see exactly why each was retrieved")
        top = response.results[0]
        for reason in top.explanation.reasons:
            out.print(f"     [pv.muted]why: {reason}[/]")
        out.print("     [pv.muted]score breakdown:[/]")
        for name, value, contribution in top.explanation.breakdown.as_table():
            if value or contribution:
                out.print(f"       [pv.muted]{name:<14} {value:>6.3f} "
                          f"x weight = {contribution:+.3f}[/]")
        out.print(f"       [pv.muted]{'TOTAL':<14} {top.explanation.breakdown.total:>6.3f}[/]")

        # Digest ---------------------------------------------------------
        digest = response.digest(char_budget=1200)
        out.print("\n[pv.heading]The digest an agent would receive[/]")
        out.print("[pv.muted]banner first, always; every item labelled; hard budget[/]\n")
        for line in digest.text.splitlines():
            if line.startswith("Historical context") or line.startswith("Treat this"):
                out.print(f"  [pv.warning]{line}[/]")
            elif line.startswith("##"):
                out.print(f"  [pv.heading]{line}[/]")
            else:
                out.print(f"  {line}")
        out.print(f"\n  [pv.muted]{digest.chars_used}/{digest.char_budget} characters, "
                  f"{digest.omitted_count} omitted[/]")

        # Audit ----------------------------------------------------------
        report = pv.audit()
        out.print(f"\n[pv.heading]Audit[/]  {report.summary()}")
        out.print(f"  [pv.provenance]chain head {report.chain_head[:26]}[/]")

        elapsed = time.monotonic() - started
        out.print(f"\n[pv.success]Done[/] in {elapsed:.1f}s — no API key, no network.")
        out.print("[pv.muted]Every line above came from the real engine: same storage, "
                  "same policy, same retrieval.[/]")

        summary: dict[str, Any] = {
            "elapsed_s": round(elapsed, 3),
            "events": pv.journal.count(project_id=DEMO_PROJECT),
            "warning_matched": warning.matched,
            "procedure_trust": procedure.trust_state.value,
            "gotcha_occurrences": pv.memories.get(gotcha.memory_id).content.get(  # type: ignore[union-attr]
                "occurrences", 1
            ),
            "digest_chars": digest.chars_used,
            "digest_within_budget": digest.within_budget,
            "audit_ok": report.ok,
            "chain_head": report.chain_head,
            "workdir": str(workdir),
        }

        if html_out is not None:
            html_out.parent.mkdir(parents=True, exist_ok=True)
            html_out.write_text(_render_html(pv, digest.text, report, summary))
            out.print(f"[pv.muted]HTML report written to {html_out}[/]")

        pv.close()
        return summary

    finally:
        if not keep:
            shutil.rmtree(workdir, ignore_errors=True)


def _render_html(pv: Any, digest_text: str, report: Any, summary: dict[str, Any]) -> str:
    """Render a light-themed HTML report.

    Light-first per ADR-0018 — white and beige backgrounds, black text, green for
    action, mauve for lineage, gold reserved for attested states. Gold appears
    only as a badge border on white, never as body text and never on beige, which
    is what the measured contrast ratios permit.
    """
    from provalume.schemas.trust import TrustState

    tokens = Path(__file__).resolve().parents[3] / "docs" / "design" / "tokens.css"
    css = tokens.read_text() if tokens.exists() else _FALLBACK_CSS

    rows: list[str] = []
    for memory in pv.memory_records(include_terminal=True, current_only=False, limit=50):
        state = memory.trust_state.value
        badge = "pv-badge--" + (
            "verified"
            if memory.trust_state
            in {TrustState.VERIFIED, TrustState.REVIEWED, TrustState.INTEGRATED}
            else state
        )
        rows.append(
            f"<tr><td><span class='pv-badge {badge}'>{_escape(state)}</span></td>"
            f"<td><code>{_escape(memory.memory_type.value)}</code></td>"
            f"<td>{_escape(memory.text)}</td></tr>"
        )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Provalume demo report</title>
<style>{css}
.pv-wrap {{ max-width: 60rem; margin: 0 auto; padding: var(--pv-space-8); }}
.pv-digest {{ background: var(--pv-surface-code);
  border-left: var(--pv-border-seal) solid var(--pv-mauve);
  padding: var(--pv-space-4); white-space: pre-wrap; font-family: var(--pv-font-mono);
  font-size: var(--pv-text-sm); overflow-x: auto; }}
table {{ width: 100%; }}
td {{ vertical-align: top; }}
</style></head>
<body><div class="pv-wrap">
<h1>Provalume demo</h1>
<p><strong>Facts your agents proved, not things they said.</strong></p>
<p>Generated offline in {summary['elapsed_s']}s from {summary['events']} journal events.
No API key, no network, no LLM.</p>

<div class="pv-card">
<h2>Audit</h2>
<p>{_escape(report.summary())}</p>
<p><span class="pv-lineage">chain head <code>{_escape(report.chain_head[:32])}</code></span></p>
</div>

<h2>The digest an agent receives</h2>
<div class="pv-untrusted-banner">
Every digest opens with this banner. Retrieved memory is data, never instruction.
</div>
<div class="pv-digest">{_escape(digest_text)}</div>

<h2>Memory records</h2>
<table><thead><tr><th>Trust</th><th>Type</th><th>Text</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>

<p class="pv-lineage"><small>Gold marks attested states, mauve marks lineage,
green marks action. Colour is reinforcement; every state is labelled in text.</small></p>
</div></body></html>
"""


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_FALLBACK_CSS = """
:root { --pv-warm-white:#FCFAF5; --pv-white:#FFFFFF; --pv-beige-soft:#E9DFC9;
 --pv-beige-light:#F3ECDD; --pv-black:#151515; --pv-green:#3F684F;
 --pv-mauve:#705468; --pv-gold:#B28A45; --pv-surface:var(--pv-warm-white);
 --pv-surface-code:var(--pv-beige-light); --pv-border:var(--pv-beige-soft);
 --pv-space-4:1rem; --pv-space-8:2rem; --pv-border-seal:3px;
 --pv-font-mono:ui-monospace,Menlo,monospace; --pv-text-sm:0.875rem; }
body { background:var(--pv-surface); color:var(--pv-black);
 font-family:ui-sans-serif,system-ui,sans-serif; line-height:1.6; }
.pv-card { background:var(--pv-white); border:1px solid var(--pv-border);
 border-radius:10px; padding:1.5rem; }
.pv-badge { display:inline-block; font-size:0.875rem; padding:0.25rem 0.5rem;
 border:2px solid var(--pv-border); border-radius:3px; background:var(--pv-white);
 color:var(--pv-black); }
.pv-badge--verified { border-color:var(--pv-gold); }
.pv-badge--observed { border-color:var(--pv-green); }
.pv-badge--superseded,.pv-badge--invalidated,.pv-badge--rejected { border-color:var(--pv-mauve); }
.pv-untrusted-banner { background:var(--pv-beige-light);
 border-left:3px solid var(--pv-mauve); padding:0.75rem 1rem; font-size:0.875rem; }
.pv-lineage { color:var(--pv-mauve); }
th { background:var(--pv-beige-light); text-align:left; }
td,th { padding:0.5rem 0.75rem; border-bottom:1px solid var(--pv-border); }
"""
