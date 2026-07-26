"""Structural classification: what kind of semantic change is this?

The stage after trivia (spec §5.3): every reason here escalates; this module
only decides which name the escalation carries, because a re-verification
decision (M4) and a precision measurement (M5) both want to know whether a
change touched an interface, the import surface, or a body. Everything here
is stdlib ``ast`` (I1).

Contract (frozen at the M3 skeleton; ``relevance.assess_file`` calls exactly
this):

``classify(pre_tree: ast.Module, post_tree: ast.Module) -> ReasonCode``

- Called only when both trees parsed AND their docstring-stripped dumps
  differ. Total: always returns one of ``SIGNATURE_CHANGED``,
  ``IMPORT_CHANGED``, ``BODY_CHANGED``. Never raises; an internal failure
  returns ``BODY_CHANGED`` (escalation is already guaranteed; the name just
  degrades).
- Priority when several apply: ``SIGNATURE_CHANGED`` > ``IMPORT_CHANGED`` >
  ``BODY_CHANGED``.
- ``SIGNATURE_CHANGED``: the set of definition signatures differs. A
  signature is the qualified name plus, for functions, the full argument
  spec (including defaults, annotations, * / ** markers), decorators, and
  the return annotation; for classes, the qualified name, bases, keywords,
  and decorators. Qualified names nest (``Outer.method``). A definition
  added, removed, or renamed is a signature change.
- ``IMPORT_CHANGED``: signatures equal, but the multiset of import
  statements (module, imported names, aliases, relative level) differs.
- ``BODY_CHANGED``: everything else.
- Deterministic: comparisons over sorted/canonical forms only.
"""

from __future__ import annotations

import ast

from provalume.schemas.freshness import ReasonCode


def classify(pre_tree: ast.Module, post_tree: ast.Module) -> ReasonCode:
    """Not yet implemented (M3 fan-out unit); the honest degradation is the
    broadest escalating name."""
    return ReasonCode.BODY_CHANGED
