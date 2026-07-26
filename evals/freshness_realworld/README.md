# Real-history freshness measurement

Measures the freshness axis against **computed** ground truth: real
repositories, real commits, and the record's own command actually executed
at every replayed commit. No labeling agent is involved in the stale axis.

## Protocol (locked before any number existed — session DECISIONS D21)

- Repos qualified for: permissive license, zero/light dependencies, flat
  layout (`python -m pytest` works from a checkout), suite green at HEAD
  under the harness interpreter (a dedicated venv — pytest + coverage
  only, **not** under a temp directory: `normalize_command` rewrites temp
  paths to `<TMP>`, which blocks promotion and therefore the executor).
- `N = HEAD~60` on `--first-parent`; every first-parent commit N..HEAD is
  replayed in order (subsampled at an even stride only if the projected
  cost exceeds ~25 min/repo; the stride is recorded in the results).
- Records at N: one per test file (up to 10, evenly spaced), kept only if
  green at N, recorded through the real SDK path. Even-indexed records
  get an explicit coverage-method radius; the rest keep the automatic
  import_graph/commit_touch radius.
- Per replayed commit: checkout + `git clean` (bytecode staleness, D18),
  `process_landed_commit`, re-verify every suspect or stale record
  (harness-only `allowlist=("*",)`; the shipped default stays off), then
  ground truth = run the record's command (120 s timeout, clean caches):
  exit 0 → pass, non-zero → fail, timeout/signal → error (excluded,
  counted).

## Reading the numbers

`report.py` prints Wilson 95% intervals. The unit is a (record, commit)
point; points are **correlated** within record and within commit, so the
intervals understate uncertainty. Failure points cluster (a breakage
persists across commits until fixed), so `n(truth=fail)` overstates the
number of independent breakage *episodes* — the per-episode count is in
the session report.

"Suspect precision at scan" is the judgment-laden analogue from the
synthetic corpus, computed behaviorally here: the fraction of fresh
suspect markings whose commit actually broke the command. A suspect on a
still-passing change is the **designed** over-trigger (spec §5.3), so this
is a cost measure, not a defect count.

Selection bias, stated plainly: repos and test files that do not run
green under the harness interpreter (Python 3.14) at N are dropped, so
the corpus over-represents actively maintained, compatibility-clean code.
Results are machine- and interpreter-specific; the committed
`results/*.json` are the record of one measurement, not a benchmark.
