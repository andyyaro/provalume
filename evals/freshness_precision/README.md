# Freshness precision measurement (M5)

Measures whether code-grounded invalidation (ADR-0020) is precise: how
often the system flags records whose facts genuinely stopped holding, and
how often it flags records that are still fine.

## Protocol

1. `run.py --export` emits `labeling/cases.json` — the claim, the command,
   and the pre/post file contents per case. Nothing else.
2. An **independent labeler that has never seen the implementation** labels
   every case per `LABELING.md`, producing `labeling/labels.json`.
3. `run.py` runs every case through the real path: verification recorded
   via the SDK, commit landed, `provalume freshness` scan, then a re-run
   with a harness-only `allowlist=("*",)` (the shipped default stays off).
4. `score.py` joins results with labels and computes the metrics fixed in
   advance: stale precision (kill floor 0.80), suspect precision
   (informational), recall, and the false-current case list.

The corpus taxonomy in `corpus.py` is drawn from kinds of changes
developers land, not from the differ's code paths — see its docstring.

## Caveat — read before citing any number

The ground-truth labels are **agent-generated** and warrant human
spot-checking before any public claim is made from them. No efficacy claim
derived from these labels may enter the README, the CHANGELOG, or any
published material without that human review. `labeling/labels.json`
records the labeler's rationale per case for exactly that audit.
