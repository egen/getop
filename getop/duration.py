"""Parse --since durations like 30m, 1h, 24h, 7d into start timestamps."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
_PATTERN = re.compile(r"^(\d+)([smhdw])$")


def parse_duration(since: str) -> timedelta:
    """Parse '1h' / '24h' / '7d' style strings into a timedelta."""
    m = _PATTERN.match(since.strip().lower())
    if not m:
        raise ValueError(
            f"Invalid --since value {since!r}: expected <number><unit> "
            "with unit one of s, m, h, d, w (e.g. 1h, 24h, 7d)."
        )
    value, unit = int(m.group(1)), m.group(2)
    return timedelta(**{_UNITS[unit]: value})


def since_timestamp(since: str) -> datetime:
    """UTC start timestamp for a --since window ending now."""
    return datetime.now(timezone.utc) - parse_duration(since)


def since_rfc3339(since: str) -> str:
    """RFC 3339 string (Zulu) for use in Cloud Logging filters."""
    return since_timestamp(since).strftime("%Y-%m-%dT%H:%M:%SZ")
