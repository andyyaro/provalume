"""Recording evidence and retrieving it, end to end.

    python examples/standalone/basic_usage.py

No API key, no network, no agent CLI.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from provalume import Provalume

FAILING = "pytest -n auto tests/integration"
WORKING = "pytest -p no:xdist tests/integration"


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        pv = Provalume.open(Path(tmp) / "provalume.db", project_id="example",
                            use_git=False)

        # 1. Evidence. A verification failed — this is what a gotcha is made of.
        pv.record_verification(
            command=FAILING,
            passed=False,
            excerpt="E   TimeoutError: deadlock in db fixture teardown",
            error_kind="test_failure",
            task_id="task-1",
        )

        # 2. Ask before repeating it.
        warning = pv.preflight(
            command=FAILING,
            error_kind="test_failure",
            error_text="E   TimeoutError: deadlock in db fixture teardown",
        )
        print(warning.summary if warning.matched else "no prior failure matched")

        # 3. Record what worked. The gotcha learns its own resolution.
        pv.record_verification(command=WORKING, passed=True,
                               purpose="the integration suite", task_id="task-1")

        # 4. An independent reviewer approves, and it lands. Only now can the
        #    procedure reach `integrated`.
        pv.record_review(reviewer="reviewer-2", approved=True, task_id="task-1")
        pv.record_integration(commit_sha="a" * 40, target="user", task_id="task-1")

        # 5. Retrieve, with reasons.
        print("\n--- recall ---")
        for result in pv.recall("integration tests", limit=5):
            print(f"[{result.trust_state}] {result.text}")
            if result.provenance_summary:
                print(f"    evidence: {result.provenance_summary}")

        # 6. What an agent would actually receive.
        print("\n--- digest ---")
        print(pv.recall("integration tests").digest(char_budget=1200).text)

        # 7. Why one record is trusted.
        procedures = pv.memory_records(memory_types=["procedural"], limit=1)
        if procedures:
            provenance = pv.explain(procedures[0].memory_id)
            print(f"\n--- provenance ---\n{provenance.describe()}")  # type: ignore[union-attr]

        report = pv.audit()
        print(f"\naudit: {report.summary()}")
        pv.close()


if __name__ == "__main__":
    main()
