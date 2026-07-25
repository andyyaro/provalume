"""FTS safety, SQL parameterisation, path confinement, and oversized input."""

from __future__ import annotations

from pathlib import Path

import pytest

from provalume.errors import OversizedInputError, PathConfinementError
from provalume.policy.admission import check_size
from provalume.policy.scope import confine, is_confined
from provalume.sdk.client import Provalume
from provalume.store.fts import MAX_TERMS, build_query, is_safe_query, tokenize

# --- FTS query safety (threat T22) -----------------------------------------

ADVERSARIAL_QUERIES = [
    'foo" OR text MATCH "bar',
    "a* b^ c: NEAR(x y)",
    '"unbalanced quote',
    "text:secret AND rowid:1",
    "((((((((((",
    "))))))))))",
    "NOT everything OR anything AND all",
    "\x00\x01 null bytes",
    "SELECT * FROM memories; DROP TABLE events;--",
    "' OR '1'='1",
    "a" * 10_000,
    "^prefix*",
    '{a b} NEAR/5 c',
    "-negated +required",
    "𝕌𝕟𝕚𝕔𝕠𝕕𝕖 𝔞𝔱𝔱𝔞𝔠𝔨",
    "\\escape\\sequences",
]


@pytest.mark.parametrize("query", ADVERSARIAL_QUERIES)
def test_adversarial_queries_produce_safe_expressions(query: str) -> None:
    """Operators are stripped, not escaped: escaping invites a bypass."""
    assert is_safe_query(build_query(query))


@pytest.mark.parametrize("query", ADVERSARIAL_QUERIES)
def test_adversarial_queries_do_not_crash_retrieval(pv: Provalume, query: str) -> None:
    pv.record_verification(command="pytest -q", passed=False, excerpt="E boom",
                           error_kind="e")
    pv.recall(query, limit=5)


def test_identifiers_survive_tokenisation() -> None:
    """This corpus is mostly commands and paths; shredding them would make the
    index useless."""
    terms = tokenize("src/main.py:42 pytest-xdist no:xdist c++ v1.2.3 --no-verify")
    assert "src/main.py:42" in terms
    assert "pytest-xdist" in terms
    assert "no:xdist" in terms
    assert "c++" in terms
    assert "v1.2.3" in terms


def test_boolean_keywords_are_dropped() -> None:
    assert tokenize("cats AND dogs OR birds NOT fish NEAR trees") == [
        "cats", "dogs", "birds", "fish", "trees"
    ]


def test_term_count_is_capped() -> None:
    assert len(tokenize(" ".join(f"term{i}" for i in range(200)))) <= MAX_TERMS


def test_empty_and_punctuation_only_queries_are_empty() -> None:
    for query in ("", "   ", "!!!@@@###", "((()))"):
        assert build_query(query) == ""


def test_and_mode_joins_conjunctively() -> None:
    assert build_query("alpha beta", mode="and") == '"alpha" AND "beta"'


def test_quote_doubling_is_applied() -> None:
    from provalume.store.fts import quote_term

    assert quote_term('say "hi"') == '"say ""hi"""'


# --- SQL parameterisation (threat T23) -------------------------------------


def test_sql_metacharacters_in_values_are_inert(pv: Provalume) -> None:
    hostile = "'; DROP TABLE events; --"
    pv.record_verification(command=hostile, passed=False, excerpt=hostile, error_kind=hostile)
    assert pv.journal.count() >= 1
    assert pv.audit().ok


def test_hostile_project_id_does_not_break_isolation(db: object) -> None:
    from provalume.sdk.client import Provalume as P

    hostile = "p' OR '1'='1"
    a = P(db, project_id=hostile, git=None)  # type: ignore[arg-type]
    b = P(db, project_id="other", git=None)  # type: ignore[arg-type]
    a.record_fact(statement="secret to a", subject="s")
    assert not b.recall("secret", limit=10).results


def test_no_public_api_accepts_raw_sql() -> None:
    """A `query`/`sql`/`where` parameter on the public surface would be the
    injection surface this design avoids having."""
    import inspect

    from provalume.sdk.client import Provalume as P

    for name, member in inspect.getmembers(P, predicate=inspect.isfunction):
        if name.startswith("_"):
            continue
        parameters = set(inspect.signature(member).parameters)
        assert "sql" not in parameters, f"{name} accepts raw SQL"
        assert "where" not in parameters, f"{name} accepts a raw WHERE clause"


# --- Path confinement (threat T21) -----------------------------------------


@pytest.mark.parametrize(
    "candidate",
    [
        "../outside.txt",
        "../../etc/passwd",
        "a/../../../escape",
        "/etc/passwd",
        "~/.ssh/id_rsa",
        "sub/../../..",
    ],
)
def test_traversal_is_refused(tmp_path: Path, candidate: str) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(PathConfinementError):
        confine(candidate, root)


@pytest.mark.parametrize("candidate", ["inside.txt", "sub/inside.txt", "./ok", "a/b/../c"])
def test_paths_within_the_root_are_allowed(tmp_path: Path, candidate: str) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert confine(candidate, root).is_relative_to(root.resolve())


def test_symlink_escape_is_refused(tmp_path: Path) -> None:
    """Resolution happens before comparison; a prefix check on an unresolved
    path is the classic bypass."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "link"
    link.symlink_to(outside)
    assert not is_confined(link / "secret.txt", root)


def test_is_confined_does_not_raise(tmp_path: Path) -> None:
    assert is_confined("ok", tmp_path)
    assert not is_confined("../nope", tmp_path)


# --- Oversized input (threat T25) ------------------------------------------


def test_oversized_payload_is_refused() -> None:
    with pytest.raises(OversizedInputError, match="over the"):
        check_size({"blob": "x" * (300 * 1024)})


def test_oversized_field_is_refused() -> None:
    with pytest.raises(OversizedInputError):
        check_size({"excerpt": "x" * 10_000})


def test_oversized_array_is_refused() -> None:
    with pytest.raises(OversizedInputError, match="item cap"):
        check_size({"items": list(range(20_000))})


def test_oversized_key_is_refused() -> None:
    with pytest.raises(OversizedInputError):
        check_size({"k" * 500: "v"})


def test_rejection_is_explicit_rather_than_silent_truncation() -> None:
    """Truncating a failure excerpt could remove the line that identifies the
    failure, producing a memory that looks complete and is not."""
    with pytest.raises(OversizedInputError):
        check_size({"excerpt": "x" * 9_000})


def test_reasonable_payloads_pass() -> None:
    check_size({"command": "pytest -q", "excerpt": "E boom\n" * 100, "exit_code": 1})
