# Brand guide

Provalume looks like an archive, not a dashboard.

The category defaults to dark backgrounds, neon accents, and a glowing brain
icon. That aesthetic says *speed and novelty*. Provalume's proposition is the
opposite — records, evidence, lineage, things that hold up over time — and a
verification-provenance system that looks like a cyberpunk console is arguing
against itself.

Decision record: [ADR-0018](../adr/ADR-0018-visual-identity-and-design-tokens.md).
Machine-readable tokens: [`tokens.json`](tokens.json), [`tokens.css`](tokens.css).

---

## Palette

| Token | Hex | Role |
|---|---|---|
| `--pv-warm-white` | `#FCFAF5` | Primary background |
| `--pv-white` | `#FFFFFF` | Cards, elevated surfaces |
| `--pv-beige-soft` | `#E9DFC9` | Borders, dividers, table headers |
| `--pv-beige-light` | `#F3ECDD` | Secondary background, code blocks |
| `--pv-black` | `#151515` | Primary typography |
| `--pv-green` | `#3F684F` | Actions, links, success |
| `--pv-mauve` | `#705468` | Lineage, provenance, supersession |
| `--pv-gold` | `#B28A45` | Verified, promoted, attested, trusted |

White and beige dominate. Black carries the text. Green is the working accent.
Mauve marks lineage. **Gold is scarce on purpose** — if everything is gold,
nothing is verified.

## Two hard accessibility rules

Measured, not estimated. `docs/design/contrast_check.py` recomputes these in CI
and fails on drift.

| On `--pv-warm-white` | Ratio | Normal text | Large text |
|---|---:|---|---|
| black | 17.51:1 | AAA | AAA |
| mauve | 6.38:1 | AA | AAA |
| green | 6.09:1 | AA | AAA |
| gold | 3.04:1 | **fails AA** | AA |

1. **Gold never sets normal-size body text.** At 3.04:1 it clears the 3.0:1 bar
   for large text and non-text elements only.
2. **Gold never appears on beige.** 2.70:1 on `--pv-beige-light` and 2.40:1 on
   `--pv-beige-soft` — below even the non-text threshold, so it fails as a border
   there too.

Consequence: **the verified state is a gold-bordered badge on white with a black
label**, never gold prose. This constraint was found by measuring rather than by
eye, which is why the check runs in CI.

Black, green, and mauve clear AA on every background in the palette and are
unconstrained.

## Colour is never the signal

Every trust state prints its name. The system must be correct in a monochrome
terminal, for a colour-blind reader, and when piped to a file.

```
* VERIFIED     `pytest -p no:xdist` succeeded
? QUARANTINED  an agent proposed this; no evidence supports it
x SUPERSEDED   the project used pip (replaced)
```

## Iconography

**Never:** brains, robots, sparkles, glowing orbs, neural networks, circuit
traces.

**Instead:** lineage and branching paths, archival records and ledgers, evidence
seals and wax marks, cairns and stacked-stone markers, provenance nodes, verified
check-marks inside a bordered field.

The mark should read *this was recorded and it holds up*, not *an AI did
something*.

## Terminal output

- **No background fills.** The user's terminal background is theirs; painting a
  warm-white panel over an unknown theme is unreadable as often as not.
- `NO_COLOR` and non-TTY output produce plain text.
- ASCII markers, not Unicode symbols, so output survives a pipe and a CI log.
- Colour rendering is approximate on a 256-colour terminal, which is another
  reason the label carries the meaning.

## Voice

Plain, specific, and willing to say what does not work.

- "Facts your agents proved, not things they said." — the tagline, everywhere.
- Name evidence, not adjectives: "verified by `pytest -q`" beats "high
  confidence".
- State costs. Every ADR lists what its decision makes worse; the limitations
  document leads with the largest weakness rather than burying it.
- No "best ever", no "revolutionary", no benchmark superiority claims.

## Dark theme

Deferred, and **never the default identity**. If added, it is an override
triggered by `prefers-color-scheme: dark` or `data-theme="dark"`, mapping the same
semantic role names to dark-appropriate values. Light stays canonical:
screenshots, social previews, and documentation default to it.

## Using the name

- **Provalume** — display. Capital P, one word.
- **provalume** — the package, the CLI, the import, the MCP server name.
- Pronounced **PROV-uh-loom**.

Not "ProValume", "Prova Lume", or "PV". Apache-2.0 permits use of the code; please
do not imply endorsement by a fork or derivative.
