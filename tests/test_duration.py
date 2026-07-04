from datetime import datetime, timedelta, timezone

import pytest

from getop.duration import parse_duration, since_rfc3339, since_timestamp


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30m", timedelta(minutes=30)),
        ("1h", timedelta(hours=1)),
        ("24h", timedelta(hours=24)),
        ("7d", timedelta(days=7)),
        ("2w", timedelta(weeks=2)),
        ("90s", timedelta(seconds=90)),
        (" 1H ", timedelta(hours=1)),
    ],
)
def test_parse_duration(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.parametrize("bad", ["", "1", "h", "1.5h", "1 h", "1y", "-1h", "1hh"])
def test_parse_duration_rejects(bad):
    with pytest.raises(ValueError):
        parse_duration(bad)


def test_since_timestamp_is_utc_and_in_past():
    ts = since_timestamp("1h")
    assert ts.tzinfo is not None
    delta = datetime.now(timezone.utc) - ts
    assert timedelta(minutes=59) < delta < timedelta(minutes=61)


def test_since_rfc3339_shape():
    text = since_rfc3339("24h")
    assert text.endswith("Z")
    datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
