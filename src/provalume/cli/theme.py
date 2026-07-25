"""Terminal styling, derived from the design tokens (ADR-0018).

The palette is light-first, warm, and archival. In a terminal that means three
rules, all of which are about not lying to the user:

1. **No background fills.** The user's terminal background is theirs. Painting a
   warm-white panel over an unknown theme produces unreadable text as often as
   not.
2. **Colour is reinforcement, never signal.** Every trust state prints its name.
   The output must be correct in a monochrome terminal, for a colour-blind
   reader, and when piped to a file.
3. **`NO_COLOR` and non-TTY output are honoured**, producing plain text.

Terminal colour rendering is approximate — ``#3F684F`` will be quantised on a
256-colour terminal — which is another reason the label carries the meaning.
"""

from __future__ import annotations

import os
import sys
from typing import Final

from rich.console import Console
from rich.theme import Theme

# Palette from docs/design/tokens.json. Kept in sync with that file, which is the
# source of truth; the contrast constraints there apply to rendered HTML rather
# than to terminal output, where the user's own background governs.
GREEN: Final = "#3F684F"
MAUVE: Final = "#705468"
GOLD: Final = "#B28A45"
BLACK: Final = "#151515"

PROVALUME_THEME: Final = Theme(
    {
        # Semantic roles
        "pv.action": f"bold {GREEN}",
        "pv.success": GREEN,
        "pv.lineage": MAUVE,
        "pv.provenance": MAUVE,
        "pv.attested": f"bold {GOLD}",
        "pv.heading": "bold",
        "pv.muted": "dim",
        "pv.warning": f"bold {GOLD}",
        "pv.error": "bold red",
        # Trust states. Gold marks the attested tier and is used sparingly, so
        # that when it appears it still means something.
        "pv.trust.quarantined": "dim",
        "pv.trust.observed": GREEN,
        "pv.trust.verified": f"bold {GOLD}",
        "pv.trust.reviewed": f"bold {GOLD}",
        "pv.trust.integrated": f"bold {GOLD}",
        "pv.trust.invalidated": f"dim {MAUVE}",
        "pv.trust.superseded": f"dim {MAUVE}",
        "pv.trust.rejected": f"dim {MAUVE}",
        # Memory categories
        "pv.type.episodic": "default",
        "pv.type.semantic": GREEN,
        "pv.type.procedural": GREEN,
        "pv.type.decision": MAUVE,
        "pv.type.gotcha": MAUVE,
        "pv.type.performance": "default",
    }
)


def make_console(*, stderr: bool = False, force_plain: bool = False) -> Console:
    """Build a console honouring ``NO_COLOR`` and non-TTY output."""
    no_color = force_plain or bool(os.environ.get("NO_COLOR"))
    return Console(
        theme=PROVALUME_THEME,
        stderr=stderr,
        no_color=no_color,
        soft_wrap=False,
        highlight=False,
    )


def trust_style(state: str) -> str:
    return f"pv.trust.{state}"


def type_style(memory_type: str) -> str:
    return f"pv.type.{memory_type}"


def trust_marker(state: str) -> str:
    """A short ASCII marker for a trust state.

    ASCII rather than Unicode symbols so the output survives a pipe, a CI log,
    and a terminal without a font for box-drawing characters. The marker
    reinforces the label; it never replaces it.
    """
    return {
        "quarantined": "?",
        "observed": "-",
        "verified": "*",
        "reviewed": "*",
        "integrated": "#",
        "invalidated": "x",
        "superseded": "x",
        "rejected": "x",
    }.get(state, "-")


def is_tty() -> bool:
    return sys.stdout.isatty()
