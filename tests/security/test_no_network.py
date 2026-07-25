"""Provalume must contain no network code.

The privacy claim is "no telemetry, no analytics, no update check, no account,
and nothing leaves your machine". A claim like that is worth nothing unless it is
mechanically checked, so this file checks it: a dependency or an import that
introduces network capability fails the build.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import provalume

SOURCE_ROOT = Path(provalume.__file__).parent

#: Modules that can open a socket. Any import of one of these on a required path
#: would falsify the privacy claim.
NETWORK_MODULES = frozenset(
    {
        "socket",
        "ssl",
        "http",
        "http.client",
        "urllib",
        "urllib.request",
        "urllib3",
        "httpx",
        "requests",
        "aiohttp",
        "websockets",
        "ftplib",
        "smtplib",
        "telnetlib",
        "xmlrpc",
        "asyncio.streams",
    }
)

#: The optional embedder backends legitimately download a model once. They are
#: never imported unless a user installs the extra and asks for them, and the
#: import sits inside the constructor rather than at module scope.
ALLOWED_LAZY_IMPORTS = frozenset({"model2vec", "fastembed", "sqlite_vec", "numpy"})


def python_files() -> list[Path]:
    return sorted(SOURCE_ROOT.rglob("*.py"))


def test_source_tree_is_non_empty() -> None:
    assert python_files(), "no source files found — the check would pass vacuously"


@pytest.mark.parametrize("path", python_files(), ids=lambda p: p.name)
def test_no_module_imports_a_network_library(path: Path) -> None:
    tree = ast.parse(path.read_text(), filename=str(path))
    offending: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if alias.name in NETWORK_MODULES or root in NETWORK_MODULES:
                    offending.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if node.module in NETWORK_MODULES or root in NETWORK_MODULES:
                offending.append(node.module)

    assert not offending, (
        f"{path.relative_to(SOURCE_ROOT)} imports network-capable module(s): "
        f"{offending}. Provalume claims to make no network connections."
    )


def test_no_hardcoded_urls_outside_documentation() -> None:
    """A URL in code is either a network call waiting to happen or a docstring
    reference; only the latter is acceptable."""
    offenders: list[str] = []

    for path in python_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            # A URL inside a docstring or a comment is fine; one inside a live
            # string constant is what this is looking for.
            is_string = isinstance(node, ast.Constant) and isinstance(node.value, str)
            if is_string and node.value.strip().startswith(("http://", "https://")):
                offenders.append(f"{path.relative_to(SOURCE_ROOT)}: {node.value[:60]}")

    allowed = ("https://provalume.dev/schema",)
    real = [o for o in offenders if not any(a in o for a in allowed)]
    assert not real, f"live URL string constants found: {real}"


def test_required_dependencies_are_not_network_clients() -> None:
    """pydantic, typer, and rich do not open sockets. If a fourth dependency
    ever appears here, it needs the same scrutiny."""
    from importlib.metadata import requires

    declared = requires("provalume") or []
    required = [
        r.split(";")[0].split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip()
        for r in declared
        if "extra ==" not in r
    ]
    assert set(required) <= {"pydantic", "typer", "rich"}, (
        f"unexpected required dependency: {sorted(set(required))}. Any new required "
        "dependency must be checked for network capability."
    )


def test_optional_backends_are_imported_lazily() -> None:
    """The embedder extras may download a model once. They must not be imported
    at module scope, or installing the extra would change startup behaviour for
    everyone."""
    vectors = SOURCE_ROOT / "retrieval" / "vectors.py"
    tree = ast.parse(vectors.read_text(), filename=str(vectors))

    module_level: list[str] = []
    for node in tree.body:  # top level only
        if isinstance(node, ast.Import):
            module_level.extend(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            module_level.append(node.module.split(".")[0])

    leaked = set(module_level) & ALLOWED_LAZY_IMPORTS
    assert not leaked, f"optional backend imported at module scope: {sorted(leaked)}"


def test_no_telemetry_or_analytics_identifiers() -> None:
    """Grep-level check for the words that would signal phone-home code."""
    banned = re.compile(
        r"\b(telemetry|analytics|posthog|segment_io|mixpanel|sentry_sdk|"
        r"phone_home|check_for_updates|report_usage)\b",
        re.IGNORECASE,
    )
    offenders: list[str] = []
    for path in python_files():
        text = path.read_text()
        for match in banned.finditer(text):
            line_start = text.rfind("\n", 0, match.start()) + 1
            line = text[line_start : text.find("\n", match.start())]
            # A docstring saying "no telemetry" is the opposite of a problem.
            if re.search(r"\bno\s+telemetry\b", line, re.IGNORECASE):
                continue
            offenders.append(f"{path.relative_to(SOURCE_ROOT)}: {line.strip()[:80]}")
    assert not offenders, f"telemetry-shaped code found: {offenders}"


def test_subprocess_use_is_limited_to_git_and_is_argv_based() -> None:
    """Read-only Git access is the only subprocess Provalume runs, and it never
    uses a shell.

    Checked against the AST rather than the source text: a substring search for
    ``shell=True`` matches the comment explaining that it is never used, which is
    the sort of false positive that teaches people to ignore a check.
    """
    offenders: list[str] = []

    for path in python_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        relative = path.relative_to(SOURCE_ROOT)

        uses_subprocess = any(
            (isinstance(node, ast.Import) and any(a.name == "subprocess" for a in node.names))
            or (isinstance(node, ast.ImportFrom) and node.module == "subprocess")
            for node in ast.walk(tree)
        )
        if not uses_subprocess:
            continue

        # Only these modules may shell out, and only to read from Git.
        if relative.parts[0] != "store" and relative.name != "scenario.py":
            offenders.append(f"unexpected subprocess use in {relative}")

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "shell" and not (
                    isinstance(keyword.value, ast.Constant) and keyword.value.value is False
                ):
                    offenders.append(f"{relative} passes shell= to a subprocess call")

    assert not offenders, f"subprocess policy violated: {offenders}"
