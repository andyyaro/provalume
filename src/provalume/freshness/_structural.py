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

Decisions this implementation makes where the contract left room. Each is
tested in ``tests/unit/test_relevance_structural.py``:

- **Multiset, never a set.** Two defs may share a name (``@overload``
  stacks, a conditional redefinition). Deleting one of two identical
  definitions is a signature change, so signatures and imports are compared
  as sorted lists — a set would call that pair equal.
- **Definitions are collected through every enclosing statement**, not only
  through definition bodies: a function under ``if TYPE_CHECKING:`` or
  inside a ``try:`` is a definition and carries a signature. Only
  ``def``/``async def``/``class`` contribute a segment to the qualified
  name, so a conditionally defined module-level ``f`` is ``f`` — where it
  would be if the condition were removed.
- **Lambdas are expressions, not definitions.** ``lambda x: x`` →
  ``lambda y: y`` is ``BODY_CHANGED``. So are ``type`` aliases,
  ``global``/``nonlocal``, and assignments of any shape.
- **A default's value is part of the signature.** ``x=1`` → ``x=2`` is
  ``SIGNATURE_CHANGED``: the call contract of ``f()`` changed, which is
  exactly the interface fact a re-verification wants to know about. The
  same holds for a decorator change alone.
- **PEP 695 type parameters are part of the signature** (``def f[T]()`` →
  ``def f[T: int]()``): a declared bound is interface, not body.
- **Imports are keyed by their enclosing definition scope**, and each
  ``alias`` is its own entry. Consequences, all deliberate:
  moving ``import x`` between module level and a function body is
  ``IMPORT_CHANGED`` (the recommended reading — the import surface of a
  scope changed even if only the timing did); reordering import statements
  within one scope is ``BODY_CHANGED`` (the surface is identical, the
  statement order is not); and wrapping a module-level import in
  ``if TYPE_CHECKING:`` is ``BODY_CHANGED``, because conditionality is a
  property of the statement, not of which names the scope imports.
- **Reordering definitions is not a signature change.** The multiset is
  unchanged, so it falls through to the import check and then to
  ``BODY_CHANGED`` — the statement order of the enclosing body is what
  actually differs.
"""

from __future__ import annotations

import ast
import logging

from provalume.schemas.freshness import ReasonCode

log = logging.getLogger("provalume.freshness")

#: The node types that own a scope and contribute a qualified-name segment.
_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def classify(pre_tree: ast.Module, post_tree: ast.Module) -> ReasonCode:
    """Name the change: signature, import surface, or body.

    Total and non-raising by construction — the priority ladder ends in
    ``BODY_CHANGED``, and so does every internal failure.
    """
    try:
        if _signatures(pre_tree) != _signatures(post_tree):
            return ReasonCode.SIGNATURE_CHANGED
        if _imports(pre_tree) != _imports(post_tree):
            return ReasonCode.IMPORT_CHANGED
    except Exception:
        # A malformed tree is not a licence to call the change harmless: the
        # broadest escalating name is the honest answer.
        log.debug("structural classification failed; naming the change body", exc_info=True)
        return ReasonCode.BODY_CHANGED
    return ReasonCode.BODY_CHANGED


# --- Definition signatures ----------------------------------------------------


def _signatures(tree: ast.Module) -> list[tuple[str, ...]]:
    """Every definition's signature, as a sorted multiset."""
    found: list[tuple[str, ...]] = []
    _collect_signatures(tree, (), found)
    return sorted(found)


def _collect_signatures(
    node: ast.AST, scope: tuple[str, ...], found: list[tuple[str, ...]]
) -> None:
    """Descend through *every* child, tracking only definition scopes.

    Descending through non-definition statements is what makes a
    conditionally defined function visible; qualifying only on definitions is
    what keeps its name the one it would have unconditionally.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _DEFINITIONS):
            qualified = (*scope, child.name)
            if isinstance(child, ast.ClassDef):
                found.append(_class_signature(child, qualified))
            else:
                found.append(_function_signature(child, qualified))
            _collect_signatures(child, qualified, found)
        else:
            _collect_signatures(child, scope, found)


def _function_signature(
    node: ast.FunctionDef | ast.AsyncFunctionDef, qualified: tuple[str, ...]
) -> tuple[str, ...]:
    return (
        "def",
        ".".join(qualified),
        "async" if isinstance(node, ast.AsyncFunctionDef) else "sync",
        _argspec(node.args),
        _dump(node.returns),
        _ordered(node.decorator_list),
        _ordered(node.type_params),
    )


def _class_signature(node: ast.ClassDef, qualified: tuple[str, ...]) -> tuple[str, ...]:
    return (
        "class",
        ".".join(qualified),
        # Base order is the MRO and therefore significant; keyword order
        # (``metaclass=``, ``total=``) is not, so it is canonicalised.
        _ordered(node.bases),
        _canonical(node.keywords),
        _ordered(node.decorator_list),
        _ordered(node.type_params),
    )


def _argspec(args: ast.arguments) -> str:
    """The full call contract: names, annotations, defaults, and markers."""
    parts: list[str] = []
    positional = [*args.posonlyargs, *args.args]
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults))
    defaults.extend(args.defaults)
    for index, (arg, default) in enumerate(zip(positional, defaults, strict=True)):
        parts.append(_param(arg, default))
        if index + 1 == len(args.posonlyargs):
            parts.append("/")
    if args.vararg is not None:
        parts.append("*" + _param(args.vararg, None))
    elif args.kwonlyargs:
        # The bare ``*`` marker: keyword-only with no varargs.
        parts.append("*")
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        parts.append(_param(arg, default))
    if args.kwarg is not None:
        parts.append("**" + _param(args.kwarg, None))
    return "(" + ", ".join(parts) + ")"


def _param(arg: ast.arg, default: ast.expr | None) -> str:
    return f"{arg.arg}:{_dump(arg.annotation)}={_dump(default)}"


def _dump(node: ast.AST | None) -> str:
    """``ast.dump`` without positions — identical text on every platform."""
    return "" if node is None else ast.dump(node)


def _ordered(nodes: list[ast.expr] | list[ast.type_param]) -> str:
    """Order-preserving canonical form, for lists whose order is meaning."""
    return "[" + ", ".join(_dump(node) for node in nodes) + "]"


def _canonical(nodes: list[ast.keyword]) -> str:
    """Order-independent canonical form, for lists whose order is not."""
    return "[" + ", ".join(sorted(_dump(node) for node in nodes)) + "]"


# --- Import surface -----------------------------------------------------------


def _imports(tree: ast.Module) -> list[tuple[str, ...]]:
    """Every imported binding, keyed by scope, as a sorted multiset."""
    found: list[tuple[str, ...]] = []
    _collect_imports(tree, "", found)
    return sorted(found)


def _collect_imports(node: ast.AST, scope: str, found: list[tuple[str, ...]]) -> None:
    for child in ast.iter_child_nodes(node):
        inner = scope
        if isinstance(child, _DEFINITIONS):
            inner = f"{scope}.{child.name}" if scope else child.name
        elif isinstance(child, ast.Import):
            for alias in child.names:
                found.append(("import", scope, alias.name, alias.asname or ""))
        elif isinstance(child, ast.ImportFrom):
            for alias in child.names:
                found.append(
                    (
                        "from",
                        scope,
                        str(child.level),
                        child.module or "",
                        alias.name,
                        alias.asname or "",
                    )
                )
        _collect_imports(child, inner, found)
