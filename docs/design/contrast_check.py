#!/usr/bin/env python3
"""Verify the Provalume palette against its documented accessibility constraints.

Run directly, or in CI:

    python docs/design/contrast_check.py

Exits non-zero if any rule in ``tokens.json`` under ``accessibility.constraints``
is violated, or if a measured ratio has drifted from the recorded value. The
recorded values in ``tokens.json`` and in ADR-0018 are the output of this script;
it exists so those numbers can never be estimates.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TOKENS = Path(__file__).parent / "tokens.json"

# WCAG 2.1 thresholds.
AA_NORMAL = 4.5
AA_LARGE = 3.0
AAA_NORMAL = 7.0
NON_TEXT = 3.0


def _linearize(channel: int) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    """WCAG 2.1 relative luminance of an ``#rrggbb`` colour."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _linearize(r) + 0.7152 * _linearize(g) + 0.0722 * _linearize(b)


def contrast(fg: str, bg: str) -> float:
    """WCAG 2.1 contrast ratio between two ``#rrggbb`` colours."""
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def main() -> int:
    tokens = json.loads(TOKENS.read_text())
    color = {name: spec["value"] for name, spec in tokens["color"].items()}
    recorded = tokens["accessibility"]["measured"]

    backgrounds = ["warm-white", "white", "beige-light", "beige-soft"]
    foregrounds = ["black", "mauve", "green", "gold"]

    failures: list[str] = []
    print("Provalume palette contrast (WCAG 2.1)\n")

    for bg in backgrounds:
        print(f"  on {bg} ({color[bg]})")
        key = f"on-{bg}"
        for fg in foregrounds:
            ratio = contrast(color[fg], color[bg])
            normal = "AAA" if ratio >= AAA_NORMAL else "AA" if ratio >= AA_NORMAL else "--"
            large = "AA" if ratio >= AA_LARGE else "--"
            print(f"    {fg:<12} {ratio:6.2f}:1   normal={normal:<4} large={large}")

            # Recorded values must match what we just computed, to 2dp.
            want = recorded.get(key, {}).get(fg)
            if want is None:
                failures.append(f"tokens.json is missing measured.{key}.{fg}")
            elif abs(round(ratio, 2) - want) > 0.005:
                failures.append(
                    f"measured.{key}.{fg} records {want} but computes {ratio:.2f}"
                )
        print()

    # --- Documented constraints, each asserted explicitly ---------------------

    # gold-not-body-text: gold must fail AA-normal on warm-white. If a future
    # palette change made gold pass, the constraint text would be wrong.
    gold_ww = contrast(color["gold"], color["warm-white"])
    if gold_ww >= AA_NORMAL:
        failures.append(
            f"constraint gold-not-body-text is stale: gold on warm-white is now "
            f"{gold_ww:.2f}:1, which passes AA for normal text"
        )
    elif gold_ww < AA_LARGE:
        failures.append(
            f"gold on warm-white is {gold_ww:.2f}:1 — below the {AA_LARGE}:1 large-text "
            f"floor, so gold is unusable even for badges"
        )

    # gold-not-on-beige: gold must NOT be placed on either beige. Assert both
    # are genuinely below the non-text threshold, which is why the rule exists.
    for beige in ("beige-light", "beige-soft"):
        ratio = contrast(color["gold"], color[beige])
        if ratio >= NON_TEXT:
            failures.append(
                f"constraint gold-not-on-beige is stale: gold on {beige} is now "
                f"{ratio:.2f}:1, at or above the {NON_TEXT}:1 non-text threshold"
            )

    # black, green, and mauve are claimed unconstrained: AA-normal everywhere.
    for fg in ("black", "green", "mauve"):
        for bg in backgrounds:
            ratio = contrast(color[fg], color[bg])
            if ratio < AA_NORMAL:
                failures.append(
                    f"{fg} on {bg} is {ratio:.2f}:1, below AA for normal text — "
                    f"ADR-0018 claims black/green/mauve are unconstrained"
                )

    # Every semantic role must name a colour that exists.
    for role, name in tokens["semantic"].items():
        if name not in color:
            failures.append(f"semantic role {role!r} names unknown colour {name!r}")

    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("All documented contrast constraints hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
