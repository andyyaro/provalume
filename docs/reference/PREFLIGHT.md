# The pre-action warning gate

Before an agent repeats a risky action, ask whether it already failed.

The idea is ProjectMem's; this is an independent implementation keyed on
deterministic failure signatures. Nothing here uses a language model.

---

## What it returns

```sh
provalume preflight --command "pytest -n auto tests/integration"
```

```
A similar approach failed previously.

  Previous attempt   pytest -n auto tests/integration
  Occurrences        failed twice
  Failure evidence   E TimeoutError: deadlock in db fixture teardown after 30.5s
  What later worked  pytest -p no:xdist tests/integration
  Applicability      current
  Provenance         attempt attempt-1; agent agent-A; branch main; commit b57ba729485c
  Trust state        verified
  Match confidence   1.00 (exact failure signature match)

  This is a warning, not a block. Provalume does not override policy.
```

Every line is evidence. The gate says what happened; it does not tell the agent
what to do.

## Warning, not blocking

**Default behaviour is to warn.** Memory must not acquire veto power over an
orchestrator's policy: a poisoning bug would become an orchestration-control bug,
and a false positive would become an outage.

`provalume preflight` exits 0 whether or not it warns. An exit code meaning
"blocked" would make it a gate that scripts route around.

Blocking exists behind an explicit policy and requires **all three**: an exact
signature match, at least two occurrences, and `allow_blocking=True`.

```python
from provalume.retrieval.preflight import PreflightGate

gate = PreflightGate(pv.memories, allow_blocking=True)
```

## Failure signatures

A signature is SHA-256 over a normalised `(command, error_kind, error_fingerprint)`
tuple.

### Command normalisation — conservative

Only whitespace and temp paths. Flags and arguments are meaningful:
`pytest -n auto` and `pytest -p no:xdist` are different procedures, and collapsing
them would let one claim the other's evidence.

### Error normalisation — aggressive

Everything that varies between two runs of the same failure is replaced with a
placeholder:

| Stripped | Replaced with |
|---|---|
| Timestamps | `<TS>`, `<TIME>` |
| Temp directories | `<TMP>` |
| Absolute paths (basename kept) | `<PATH>/name` |
| Memory addresses | `<ADDR>` |
| Long hex digests | `<HASH>`, `<SHORTHASH>` |
| UUIDs and ULIDs | `<UUID>`, `<ULID>` |
| Line and column numbers | `line <N>`, `:<N>:<N>` |
| PIDs and ports | `pid <N>`, `port <N>` |
| Durations and sizes | `<DUR>`, `<SIZE>` |
| Numbers of 2+ digits | `<N>` |

**Single digits survive.** `exit 1` and `exit 2` are genuinely different failures.

### Picking the identifying line

A Python traceback contains both the failing source line (`assert pool.acquire()`)
and the exception it raised (`E TimeoutError: deadlock`). The second is far more
identifying — the same source line can raise different exceptions — so the
fingerprint is picked in two tiers:

1. A line that **declares** an error: `ValueError: bad input`,
   `E   TimeoutError: …`, `error[E0308]: …`, `FATAL: …`,
   `Segmentation fault`, or a bare CamelCase name with a colon
   (`PoolExhausted: …`, which is how most custom exceptions are named).
2. Failing that, a line that merely mentions failure.

Framing like `Traceback (most recent call last):` is skipped, because it is
identical across unrelated failures and would collapse them onto one signature.

### The trade-off, stated plainly

Normalisation is lossy in both directions. Too aggressive and distinct failures
collide, producing a warning about something that never happened. Too timid and
the same failure never matches itself, so the gate is silent exactly when it
should speak.

Every rule is individually tested and the false-positive rate is an eval metric
(scenario 19) rather than an assumption. **Two failures sharing a signature are
*probably* the same failure — never certainly.**

## Match tiers

| Tier | Confidence | Matches on |
|---|---:|---|
| Exact signature | 1.00 | The same command failing the same way |
| Same command | 0.85 | The command is known trouble, even if it broke differently |
| Rejected alternative | 0.75 | A recorded decision already rejected this approach |
| Subsystem overlap | 0.55 | A prior failure in the same subsystem |
| File overlap | 0.40 | A prior failure touching the same file |

Below 0.40 nothing is reported. A gate that cries wolf gets ignored, which is
worse than no gate — it also teaches agents to dismiss the real warnings.

Confidence is reduced for records whose applicability is uncertain (×0.8) or
historical (×0.6). Such records still warrant a warning; they warrant a quieter
one. Dropping them would make the gate go blind after every rebase.

## What it searches

- Failure signatures, exact and by command
- Alternatives a human decision already rejected
- Gotchas overlapping the named subsystem or files
- Reviewer findings, which are stored as gotchas because the consumer is the same

**Rejected records are included deliberately.** A rejected approach is the
clearest possible "do not do this again", and the gate is precisely where that
belongs.

## Measuring whether it helps

A gate nobody can evaluate is a gate nobody should trust. Every warning is
recorded, and its outcome can be linked back:

```python
warning = pv.preflight(command="pytest -n auto tests/integration")
if warning.matched:
    print(warning.summary)

pv.record_warning_outcome(
    warning_event_id=warning.warning_event_id,
    heeded=True,              # or False
    false_positive=False,     # set when the warning was wrong
)
```

That produces `warning.shown`, `warning.heeded`, `warning.ignored`, and
`warning.false_positive` events, which feed the false-warning metric in
[`BENCHMARKS.md`](BENCHMARKS.md).

## Integrating

```python
result = pv.preflight(
    command="pytest -n auto tests/integration",
    error_kind="test_failure",     # optional, sharpens the match
    error_text="…",                # optional, enables exact-signature matching
    subsystem="integration tests",
    files=("tests/integration/test_db.py",),
)
```

Call it before dispatch and before each retry. Surface `result.summary` to the
agent; honour `result.should_block` only if you enabled blocking.
