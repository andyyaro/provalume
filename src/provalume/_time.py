"""Timestamp handling.

One format, everywhere: RFC 3339 in UTC with millisecond precision and a literal
``Z`` suffix.

    2026-07-25T14:32:01.482Z

Fixed precision matters more than it looks. Timestamps are part of the canonical
JSON that gets hashed (:mod:`provalume.interchange.hashing`), so a value that
serialises as ``…:01.482Z`` on one machine and ``…:01.482000Z`` on another would
hash differently for identical data and break cross-machine duplicate detection.

Millisecond precision is chosen to match the ULID timestamp component, so an
event's identifier and its ``recorded_at`` cannot disagree about which
millisecond it belongs to.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone

_RFC3339 = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?"
    r"(Z|[+-]\d{2}:\d{2})$"
)


def now() -> str:
    """Current time as a canonical RFC 3339 UTC string."""
    return to_rfc3339(datetime.now(UTC))


def to_rfc3339(value: datetime) -> str:
    """Format a datetime canonically. Naive input is assumed to be UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    value = value.astimezone(UTC)
    return f"{value.strftime('%Y-%m-%dT%H:%M:%S')}.{value.microsecond // 1000:03d}Z"


def parse(value: str) -> datetime:
    """Parse an RFC 3339 timestamp into an aware UTC datetime.

    Accepts any valid RFC 3339 offset and any sub-second precision, so records
    produced by other tooling still import; :func:`to_rfc3339` is what normalises
    them on the way to storage.
    """
    match = _RFC3339.match(value)
    if match is None:
        msg = f"not an RFC 3339 timestamp: {value!r}"
        raise ValueError(msg)
    year, month, day, hour, minute, second = (int(match.group(i)) for i in range(1, 7))
    fraction = match.group(7) or ""
    microsecond = int(fraction.ljust(6, "0")[:6]) if fraction else 0
    offset = match.group(8)
    tz: timezone
    if offset == "Z":
        tz = UTC
    else:
        sign = 1 if offset[0] == "+" else -1
        tz_hours, tz_minutes = int(offset[1:3]), int(offset[4:6])
        tz = timezone(timedelta(minutes=sign * (tz_hours * 60 + tz_minutes)))
    return datetime(year, month, day, hour, minute, second, microsecond, tz).astimezone(UTC)


def normalize(value: str) -> str:
    """Round-trip a timestamp into canonical form.

    Used at admission so that everything stored is byte-identical in format
    regardless of how a caller expressed it.
    """
    return to_rfc3339(parse(value))


def age_days(recorded_at: str, *, reference: str | None = None) -> float:
    """Age in fractional days, used by the recency component of ranking.

    Never negative: a record whose timestamp is in the future — clock skew, or a
    caller passing a bad ``occurred_at`` — is treated as brand new rather than
    given a recency score above 1.0, which would let a forged future timestamp
    win every ranking.
    """
    then = parse(recorded_at)
    ref = parse(reference) if reference else datetime.now(UTC)
    return max(0.0, (ref - then).total_seconds() / 86400.0)


def is_before(left: str, right: str) -> bool:
    """Whether ``left`` is strictly earlier than ``right``."""
    return parse(left) < parse(right)
