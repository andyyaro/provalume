"""Canonical serialisation and hashing.

Everything Provalume claims about provenance rests on these being exact, so the
assertions are byte equality rather than semantic equality.
"""

from __future__ import annotations

import pytest

from provalume.interchange.hashing import (
    CanonicalizationError,
    canonical_json,
    hash_content,
    hash_envelope,
    hash_payload,
    hash_text,
    short,
    verify,
)


def test_key_order_does_not_affect_output() -> None:
    a = canonical_json({"b": 1, "a": 2, "c": 3})
    b = canonical_json({"c": 3, "a": 2, "b": 1})
    assert a == b == '{"a":2,"b":1,"c":3}'


def test_nested_keys_are_sorted_recursively() -> None:
    value = {"z": {"y": 1, "x": 2}, "a": [{"q": 1, "p": 2}]}
    assert canonical_json(value) == '{"a":[{"p":2,"q":1}],"z":{"x":2,"y":1}}'


def test_no_insignificant_whitespace() -> None:
    assert " " not in canonical_json({"a": 1, "b": [1, 2]})


def test_unicode_is_emitted_literally() -> None:
    """Not \\uXXXX-escaped: escaping would make the same text hash differently
    depending on which library wrote it."""
    assert canonical_json({"k": "café → 日本"}) == '{"k":"café → 日本"}'


def test_integral_floats_collapse_to_integers() -> None:
    """1.0 and 1 must hash identically, or a caller could change a hash by
    adding a decimal point."""
    assert canonical_json({"n": 1.0}) == canonical_json({"n": 1}) == '{"n":1}'
    assert hash_payload({"n": 1.0}) == hash_payload({"n": 1})


def test_non_integral_floats_are_preserved() -> None:
    assert canonical_json({"n": 1.5}) == '{"n":1.5}'


def test_booleans_are_not_treated_as_integers() -> None:
    """bool subclasses int in Python; the normaliser must not collapse it."""
    assert canonical_json({"b": True}) == '{"b":true}'
    assert canonical_json({"b": 1}) == '{"b":1}'
    assert hash_payload({"b": True}) != hash_payload({"b": 1})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_are_refused(value: float) -> None:
    """Not valid JSON, and every parser disagrees about them."""
    with pytest.raises(CanonicalizationError):
        canonical_json({"n": value})


def test_non_string_keys_are_refused() -> None:
    with pytest.raises(CanonicalizationError):
        canonical_json({1: "a"})


def test_unserializable_types_are_refused() -> None:
    with pytest.raises(CanonicalizationError):
        canonical_json({"k": object()})


def test_hash_is_prefixed_with_algorithm() -> None:
    assert hash_payload({"a": 1}).startswith("sha256:")


def test_hash_is_stable_across_calls() -> None:
    payload = {"command": "pytest -q", "exit": 1, "nested": {"z": [1, 2, 3]}}
    assert hash_payload(payload) == hash_payload(dict(payload))


def test_verify_matches_and_rejects() -> None:
    digest = hash_payload({"a": 1})
    assert verify({"a": 1}, digest)
    assert not verify({"a": 2}, digest)


def test_envelope_hash_chains_on_predecessor() -> None:
    envelope = {"event_id": "E1", "event_type": "x", "project_id": "p"}
    first = hash_envelope(envelope, payload_hash="sha256:aa", prev_event_hash="")
    second = hash_envelope(envelope, payload_hash="sha256:aa", prev_event_hash=first)
    assert first != second, "the chain must depend on its predecessor"


def test_envelope_hash_treats_absent_and_null_identically() -> None:
    """Omission-versus-null ambiguity is where two implementations of one spec
    silently diverge."""
    with_none = hash_envelope(
        {"event_id": "E1", "branch": None}, payload_hash="h", prev_event_hash=""
    )
    without = hash_envelope({"event_id": "E1"}, payload_hash="h", prev_event_hash="")
    assert with_none == without


def test_envelope_hash_changes_with_payload_hash() -> None:
    envelope = {"event_id": "E1"}
    assert hash_envelope(envelope, payload_hash="a", prev_event_hash="") != hash_envelope(
        envelope, payload_hash="b", prev_event_hash=""
    )


def test_content_hash_covers_both_structure_and_text() -> None:
    """A projection change that altered only the rendering must be visible, or
    rebuild-determinism tests would pass while digests silently differed."""
    assert hash_content({"a": 1}, "text") != hash_content({"a": 1}, "other text")
    assert hash_content({"a": 1}, "text") != hash_content({"a": 2}, "text")


def test_hash_text_differs_from_hash_value() -> None:
    assert hash_text("abc") != hash_payload({"v": "abc"})


def test_short_abbreviates_without_prefix() -> None:
    digest = hash_text("x")
    assert short(digest, 8) == digest.removeprefix("sha256:")[:8]


def test_empty_structures_hash_consistently() -> None:
    assert hash_payload({}) == hash_payload({})
    assert canonical_json({}) == "{}"
    assert canonical_json([]) == "[]"


def test_deeply_nested_structures_survive() -> None:
    deep: dict = {"level": 0}
    node = deep
    for i in range(1, 50):
        node["child"] = {"level": i}
        node = node["child"]
    assert hash_payload(deep) == hash_payload(deep)


def test_tuples_and_lists_hash_identically() -> None:
    """JSON has one sequence type; a caller's choice of tuple or list must not
    change the hash."""
    assert hash_payload({"a": (1, 2)}) == hash_payload({"a": [1, 2]})
