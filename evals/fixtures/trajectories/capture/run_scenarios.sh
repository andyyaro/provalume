#!/bin/bash
# Regenerate the trajectory fixtures from real Orkestra dogfood runs.
#
# Requires a venv with Orkestra (the provalume-memory-integration branch) and
# the provalume extra installed:
#
#   ORKESTRA_VENV=/path/to/venv bash run_scenarios.sh [output-dir]
#
# Each scenario seeds a fresh throwaway project, runs Orkestra for real under
# the call-capture shim (sitecustomize.py in this directory), and leaves one
# calls.jsonl per scenario in the output directory. Expectations are authored
# by hand afterwards, against the captured evidence — see ../README.md.
#
# Capture is not deterministic (commit SHAs, run ids and timings change every
# time), so regenerating means re-authoring expectations. The committed
# fixtures are a frozen pair; do not regenerate casually.
set -u

VENV="${ORKESTRA_VENV:?set ORKESTRA_VENV to a venv with orkestra + provalume}"
SHIM="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$PWD/captures}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

seed() { # seed <dir> <project-name> <verify-commands-toml>
  local dir="$1" name="$2" commands="$3"
  mkdir -p "$dir/.orkestra" "$dir/tests"
  cat > "$dir/uploader.py" <<'PY'
"""A tiny uploader with no retry — the thing the run is asked to fix."""


def upload(payload: str, *, transport) -> str:
    return transport(payload)
PY
  cat > "$dir/tests/test_uploader.py" <<'PY'
import uploader


def test_upload_retries_on_transient_failure():
    """The uploader must retry once when the transport fails transiently."""
    attempts = []

    def flaky(payload):
        attempts.append(payload)
        if len(attempts) == 1:
            raise ConnectionError("transient upstream reset")
        return "ok"

    assert uploader.upload("data", transport=flaky) == "ok"
    assert len(attempts) == 2, "expected exactly one retry"
PY
  cat > "$dir/SPEC.md" <<'MD'
# Uploader retry

`uploader.upload()` has no retry. A transient transport failure loses the
payload. Add a single retry on `ConnectionError`.

## Task: implement the retry

FAKE:write:notes.md:Attempted the retry by wrapping the transport call.

Acceptance: the test suite passes.
MD
  printf '__pycache__/\n.pytest_cache/\n' > "$dir/.gitignore"
  cat > "$dir/.orkestra/config.toml" <<TOML
version = 1

[project]
name = "$name"
spec_file = "SPEC.md"

[agents.alpha]
adapter = "fake"
enabled = true

[agents.beta]
adapter = "fake"
enabled = true

[director]
agent = "alpha"

[policy]
max_concurrency = 1
max_attempts_per_task = 2
max_review_cycles = 1
require_review = true
allow_push = false
task_timeout_s = 120

[verify]
commands = [$commands]

[probes]
mode = "off"
budget = 0

[memory]
enabled = true
brief_budget_chars = 2000
preflight = true
TOML
  ( cd "$dir" && git init -q -b main . && git add -A \
    && git -c user.email=capture@provalume -c user.name=capture commit -qm seed )
}

orun() { # orun <dir> <trace> <cmd...>
  local dir="$1" trace="$2"; shift 2
  ( cd "$dir" && env -u FORCE_COLOR NO_COLOR=1 \
      PROVALUME_TRACE_OUT="$trace" PYTHONPATH="$SHIM" \
      timeout 300 "$VENV/bin/orkestra" "$@" ) >>"$dir.log" 2>&1
}

fix_uploader() {
  cat > "$1/uploader.py" <<'PY'
"""A tiny uploader that retries once on a transient transport failure."""


def upload(payload: str, *, transport) -> str:
    try:
        return transport(payload)
    except ConnectionError:
        return transport(payload)
PY
  ( cd "$1" && git add -A && git -c user.email=capture@provalume \
      -c user.name=capture commit -qm fix )
}

commit_all() {
  ( cd "$1" && git add -A && git -c user.email=capture@provalume \
      -c user.name=capture commit -qm "$2" )
}

PYTEST_CMD="\"$VENV/bin/python -m pytest -q tests/\""
mkdir -p "$OUT"

# --- fix-lands: fail -> fix -> land ------------------------------------------
P="$WORK/fix-lands"; T="$OUT/fix-lands.calls.jsonl"; rm -f "$T"
seed "$P" traj-fix-lands "$PYTEST_CMD"
orun "$P" "$T" run --offline
fix_uploader "$P"
orun "$P" "$T" run --offline
echo "fix-lands: $(wc -l < "$T") calls"

# --- repeat-blocked: fail -> fail again -> human abort -----------------------
P="$WORK/repeat-blocked"; T="$OUT/repeat-blocked.calls.jsonl"; rm -f "$T"
seed "$P" traj-repeat-blocked "$PYTEST_CMD"
orun "$P" "$T" run --offline
orun "$P" "$T" run --offline
DEC=$( cd "$P" && env -u FORCE_COLOR NO_COLOR=1 "$VENV/bin/orkestra" decisions 2>/dev/null \
  | grep -oE '^dec_[a-f0-9]+' | tail -1 )
[ -n "${DEC:-}" ] && orun "$P" "$T" approve "$DEC" --option abort
echo "repeat-blocked: $(wc -l < "$T") calls (decision: ${DEC:-none})"

# --- env-gotcha: dependency-shaped failure -> fix -> land --------------------
P="$WORK/env-gotcha"; T="$OUT/env-gotcha.calls.jsonl"; rm -f "$T"
seed "$P" traj-env-gotcha "$PYTEST_CMD"
fix_uploader "$P" >/dev/null
cat > "$P/tests/test_uploader.py" <<'PY'
import reqwests  # noqa: F401 - dependency typo, the thing this run must fix

import uploader


def test_upload_retries_on_transient_failure():
    """The uploader must retry once when the transport fails transiently."""
    attempts = []

    def flaky(payload):
        attempts.append(payload)
        if len(attempts) == 1:
            raise ConnectionError("transient upstream reset")
        return "ok"

    assert uploader.upload("data", transport=flaky) == "ok"
    assert len(attempts) == 2, "expected exactly one retry"
PY
cat > "$P/SPEC.md" <<'MD'
# Broken test import

`tests/test_uploader.py` imports `reqwests`, which does not exist. The suite
cannot even be collected. Remove the bogus import.

## Task: fix the test import

FAKE:write:notes.md:Investigated the import error in the test module.

Acceptance: the test suite passes.
MD
commit_all "$P" "seed env-gotcha"
orun "$P" "$T" run --offline
/usr/bin/sed -i '' '/import reqwests/d' "$P/tests/test_uploader.py" 2>/dev/null \
  || sed -i '/import reqwests/d' "$P/tests/test_uploader.py"
commit_all "$P" "fix import"
orun "$P" "$T" run --offline
echo "env-gotcha: $(wc -l < "$T") calls"

# --- two-command-gate: lint passes, pytest fails -> fix -> land --------------
P="$WORK/two-command-gate"; T="$OUT/two-command-gate.calls.jsonl"; rm -f "$T"
seed "$P" traj-two-command-gate \
  "\"$VENV/bin/python lint_ok.py\", $PYTEST_CMD"
printf 'print("lint ok")\n' > "$P/lint_ok.py"
commit_all "$P" "seed two-command"
orun "$P" "$T" run --offline
fix_uploader "$P"
orun "$P" "$T" run --offline
echo "two-command-gate: $(wc -l < "$T") calls"

echo "captures in $OUT — author expectations.json against this evidence"
