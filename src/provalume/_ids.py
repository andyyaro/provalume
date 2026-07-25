"""Collision-resistant, time-sortable identifiers.

Provalume needs identifiers that are:

* **time-sortable**, so JSONL exports append near the end of the file and Git
  diffs stay append-mostly rather than churning (ADR-0011);
* **collision-resistant across machines**, because two people can record events
  offline and later merge without coordination (ADR-0002);
* **URL- and filename-safe**, and readable enough to paste into a bug report.

This is a ULID: 48 bits of millisecond timestamp followed by 80 bits of
randomness, Crockford base32 encoded to 26 characters. The implementation is
about forty lines, so it is here rather than in a dependency.

The ULID is the *identity* of a record. It is deliberately not derived from
content: two genuinely distinct events with identical payloads (the same command
failing twice in the same millisecond) must not collide. Content-based dedup is a
separate concern handled by ``payload_hash`` in :mod:`provalume.interchange.hashing`.
"""

from __future__ import annotations

import os
import time

# Crockford base32: no I, L, O, or U, so a transcribed identifier is hard to
# garble and impossible to turn into an accidental word.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE = {c: i for i, c in enumerate(_ALPHABET)}

_TIME_CHARS = 10
_RANDOM_CHARS = 16
ULID_LENGTH = _TIME_CHARS + _RANDOM_CHARS

_MAX_TIME_MS = (1 << 48) - 1


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def new_id(*, timestamp_ms: int | None = None) -> str:
    """Return a new 26-character ULID.

    ``timestamp_ms`` is for tests and deterministic replay only; production
    callers leave it unset.
    """
    ts = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    if not 0 <= ts <= _MAX_TIME_MS:
        msg = f"timestamp out of ULID range: {ts}"
        raise ValueError(msg)
    randomness = int.from_bytes(os.urandom(10), "big")
    return _encode(ts, _TIME_CHARS) + _encode(randomness, _RANDOM_CHARS)


def is_valid(value: str) -> bool:
    """Return whether ``value`` is a well-formed ULID."""
    if len(value) != ULID_LENGTH:
        return False
    return all(c in _DECODE for c in value)


def timestamp_ms(value: str) -> int:
    """Extract the embedded millisecond timestamp from a ULID."""
    if not is_valid(value):
        msg = f"not a valid identifier: {value!r}"
        raise ValueError(msg)
    result = 0
    for c in value[:_TIME_CHARS]:
        result = (result << 5) | _DECODE[c]
    return result


class MonotonicIdFactory:
    """Generates ULIDs that strictly increase, even within one millisecond.

    Plain :func:`new_id` sorts correctly across milliseconds but two IDs minted
    in the same millisecond have an arbitrary relative order. Where insertion
    order must be recoverable from the ID alone — journal append being the case
    that matters — this factory increments the random component instead of
    redrawing it, so ordering holds at sub-millisecond resolution.

    Not thread-safe by design: Provalume is single-writer (ADR-0003).
    """

    def __init__(self) -> None:
        self._last_ms = -1
        self._last_random = 0

    def new_id(self, *, timestamp_ms: int | None = None) -> str:
        ts = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
        if ts == self._last_ms:
            self._last_random += 1
            if self._last_random >= (1 << 80):
                # Overflowing 80 bits inside one millisecond is not reachable in
                # practice; step the clock rather than emit a duplicate.
                ts += 1
                self._last_ms = ts
                self._last_random = int.from_bytes(os.urandom(10), "big")
        else:
            # A clock that moved backwards must not produce descending IDs.
            ts = max(ts, self._last_ms)
            if ts == self._last_ms:
                self._last_random += 1
            else:
                self._last_ms = ts
                self._last_random = int.from_bytes(os.urandom(10), "big")
        return _encode(ts, _TIME_CHARS) + _encode(self._last_random, _RANDOM_CHARS)
