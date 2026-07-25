# ADR-0018: Visual identity and design tokens

**Status:** Accepted · **Date:** 2026-07-25

## Context

Developer tooling in 2026 defaults to dark backgrounds, neon accents, and a glowing
brain or sparkle icon. That aesthetic communicates *speed and novelty*. Provalume's
proposition is the opposite: **records, evidence, lineage, things that hold up over
time.** An archival system that looks like a cyberpunk dashboard is arguing against
itself.

Provalume also ships primarily as a CLI in 0.1.0 but generates HTML reports and will
eventually have documentation pages. Deciding the visual system once, as tokens,
avoids three inconsistent implementations.

## Decision

**A light, warm, archival visual system. Light-first is the identity, not a theme
choice.**

### Palette

| Token | Hex | Role |
|---|---|---|
| `--pv-warm-white` | `#FCFAF5` | Primary background |
| `--pv-white` | `#FFFFFF` | Cards, elevated surfaces |
| `--pv-beige-soft` | `#E9DFC9` | Borders, dividers, table headers |
| `--pv-beige-light` | `#F3ECDD` | Secondary background, code blocks |
| `--pv-black` | `#151515` | Primary typography |
| `--pv-green` | `#3F684F` | Primary functional accent |
| `--pv-mauve` | `#705468` | Secondary categories, lineage, supersession chains |
| `--pv-gold` | `#B28A45` | Verified, promoted, attested, trusted states — **used sparingly** |

### Semantic assignment

Colour carries meaning here, so the mapping is fixed rather than decorative:

| Meaning | Colour |
|---|---|
| Verified / promoted / integrated / attested | gold |
| Lineage, supersession, provenance chains | mauve |
| Actions, links, success, primary UI | green |
| Body text, structure | black on warm white |
| Quarantined / untrusted | black text with a beige-soft rule, **never colour alone** |

**Gold is scarce on purpose.** If everything is gold, nothing is verified. Gold marks
the state the whole project is about; overusing it would flatten the one distinction
that matters.

### Accessibility

Contrast ratios computed (WCAG 2.1 relative luminance), not estimated. The script is
in `docs/design/contrast_check.py` and runs in CI.

On `--pv-warm-white` `#FCFAF5`, the primary background:

| Foreground | Ratio | Normal text | Large text |
|---|---:|---|---|
| black `#151515` | 17.51:1 | AAA | AAA |
| mauve `#705468` | 6.38:1 | AA | AAA |
| green `#3F684F` | 6.09:1 | AA | AAA |
| gold `#B28A45` | 3.04:1 | **fails AA** | AA |

Two constraints fall out of this, and both are recorded in `tokens.json` itself so
they cannot be lost by someone reading only the palette:

1. **Gold never sets normal-size body text.** At 3.04:1 on warm white it clears the
   3.0:1 bar for large text and non-text elements only. Gold is for badges, large
   labels, icons, and borders.
2. **Gold is never placed on a beige background.** On `--pv-beige-light` it measures
   2.70:1 and on `--pv-beige-soft` 2.40:1 — below the 3.0:1 non-text threshold, so it
   fails even as a border or a large label there. Gold badges sit on warm white or
   pure white, with a beige surround at most.

Black, green, and mauve clear AA on every background in the palette, including
`--pv-beige-soft` (13.79:1, 4.80:1, 5.02:1 respectively), so they are unconstrained.

Trust states are never conveyed by colour alone: every badge carries a text label, so
the system works in a monochrome terminal, for a colour-blind reader, and when piped
to a file.

### Iconography

**Never:** brains, robots, sparkles, glowing orbs, neural networks, circuit traces.

**Instead:** lineage and branching paths, archival records and ledgers, evidence
seals and wax-seal marks, cairns and stacked-stone markers, provenance nodes,
verified check-marks inside a bordered field.

The reasoning is the same as the palette's: the icon should say *this was recorded
and it holds up*, not *an AI did something*.

### Deliverables

| Artifact | Contents |
|---|---|
| `docs/design/tokens.json` | Machine-readable tokens: colour, type, spacing, radius, semantic roles |
| `docs/design/tokens.css` | CSS custom properties, light-first with a `prefers-color-scheme: dark` **override only** |
| `docs/design/BRAND.md` | Usage guide, do/don't, contrast constraints, icon direction |
| `src/provalume/cli/theme.py` | The Rich theme mapping the palette to terminal styles |
| `docs/design/contrast_check.py` | Recomputes every ratio above and exits non-zero on a violation |

Tokens are the single source of truth. The Rich theme, generated HTML, and any future
documentation site all read from the same names.

### Terminal rendering

Rich styles derive from the same semantic roles. Terminal colour is constrained —
`#3F684F` may render approximately on a 256-colour terminal — so:

- Every state is labelled in text, colour is reinforcement.
- `NO_COLOR` and non-TTY output are honoured, producing plain text.
- No background fills in terminal output. The user's terminal background is theirs,
  and painting over it is both hostile and unreadable against unknown themes.

### Dark theme

**Deferred, and never the default identity.** If added, it is an override in
`tokens.css` triggered by `prefers-color-scheme: dark` or an explicit
`data-theme="dark"`, mapping the same semantic roles to dark-appropriate values.
Light remains the canonical identity: screenshots, social previews, and documentation
default to it.

## Consequences

**Good.** A distinctive identity in a category that all looks alike. The visual
language reinforces the product claim instead of contradicting it. One token source
means the CLI, HTML reports, and future docs agree. Accessibility is designed in
rather than audited later.

**Bad.** Light-first will read as unfashionable to part of the audience, and some
developers strongly prefer dark tooling. Accepted: identity coherence over
preference-matching, and a dark override is available later.

**Bad.** Gold's contrast limitation is a real constraint on the most semantically
important colour, and it is tighter than it first appeared: gold cannot set normal
body text anywhere, and cannot be used at all on the two beige backgrounds. So
"verified" is expressed as a gold-bordered badge on white with a black text label,
never as gold prose. The constraint was found by measuring rather than by eye, which
is why `contrast_check.py` runs in CI.

**Also bad.** Investing in a visual system for a CLI-first 0.1.0 is work that mostly
pays off later. Small cost — tokens plus a Rich theme — against three inconsistent
implementations later.

## Alternatives rejected

**Dark-first, like the rest of the category.** Contradicts the archival proposition
and looks like everything else.

**No visual system until there is a UI.** Guarantees the CLI, the HTML reports, and
the eventual docs site each invent their own.

**A generic AI icon.** Says "another AI tool", which is the positioning
[ADR-0001](ADR-0001-identity-and-scope.md) exists to avoid.

**Colour-only state encoding.** Fails in monochrome terminals, for colour-blind
readers, and when piped to a file.
