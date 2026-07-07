"""active-user derivation, panel isolation and rendering for `getop top`."""

from datetime import datetime, timedelta, timezone
from io import StringIO

from conftest import FakeClients, engine, user_license
from rich.console import Console

from getop.commands import top

_NOW = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)


def _license(last_login: datetime | None, state: str = "ASSIGNED") -> dict:
    return {
        "user_principal": "u@example.com",
        "license_assignment_state": state,
        "last_login_time": last_login.isoformat() if last_login else None,
    }


def test_active_users_trailing_windows():
    licenses = [
        _license(_NOW - timedelta(hours=2)),   # counts in 24h/7d/30d
        _license(_NOW - timedelta(days=3)),    # 7d/30d
        _license(_NOW - timedelta(days=20)),   # 30d only
        _license(_NOW - timedelta(days=40)),   # outside every window
        _license(None),                        # never logged in
    ]
    users = top.active_users(licenses, now=_NOW)
    assert users["dau"] == 1
    assert users["wau"] == 2
    assert users["mau"] == 3
    assert users["assigned"] == 5
    assert users["licensed_users"] == 5
    assert users["never_logged_in"] == 1


def test_active_users_tolerates_naive_and_garbage_timestamps():
    licenses = [
        _license(None),
        {"last_login_time": "not-a-timestamp", "license_assignment_state": "ASSIGNED"},
        # naive timestamp: treated as UTC, not a crash
        {"last_login_time": (_NOW - timedelta(hours=1)).replace(tzinfo=None).isoformat(),
         "license_assignment_state": "ASSIGNED"},
    ]
    users = top.active_users(licenses, now=_NOW)
    assert users["dau"] == 1
    assert users["mau"] == 1


def test_active_users_empty():
    users = top.active_users([])
    assert users == {
        "dau": 0, "wau": 0, "mau": 0,
        "assigned": 0, "licensed_users": 0, "never_logged_in": 0,
    }


def test_collect_top_isolates_panel_failures():
    """FakeClients has no monitoring client, so the stats/quota panels fail;
    licenses and armor (logging) must still populate."""
    clients = FakeClients(
        engines=[engine("e-1")],
        rest_responses={"/collections": {"collections": []}},
        user_licenses=[user_license("alice@example.com")],
        log_entries=[],
    )
    data = top.collect_top(clients, since="1h")
    assert data["active_users"]["licensed_users"] == 1
    assert data["armor"] == []
    assert "stats" in data["errors"]
    assert "quota" in data["errors"]
    assert data["stats"] is None


def _sample_data() -> dict:
    return {
        "project": "test-project",
        "location": "global",
        "since": "1h",
        "refreshed": _NOW.isoformat(),
        "active_users": {
            "dau": 3, "wau": 10, "mau": 25,
            "assigned": 40, "licensed_users": 45, "never_logged_in": 5,
        },
        "connectors": [
            {"id": "c1", "data_source": "sharepoint", "state": "ACTIVE",
             "last_sync_time": (_NOW - timedelta(hours=2)).isoformat()},
            {"id": "c2", "data_source": "jira", "state": "PAUSED", "last_sync_time": None},
        ],
        "stats": {
            "query_volume": {
                "m1": {"type": "x/request_count", "aggregate": 1234.0,
                       "aggregate_label": "sum"},
            },
            "latency": {},
        },
        "quota": [
            {"quota": "search_requests", "location": "global",
             "percent_used": 91.0, "exceeded": 2},
        ],
        "armor": [
            {"filter": "pi_and_jailbreak", "hits": 4,
             "last_seen": "2026-07-06T09:00:00+00:00"},
        ],
        "errors": {},
    }


def test_render_top_smoke():
    console = Console(file=StringIO(), width=150, force_terminal=False)
    console.print(top._render_top(_sample_data(), interval=30.0))
    out = console.file.getvalue()
    assert "getop top — test-project" in out
    assert "Active 24h" in out
    assert "request_count" in out
    assert "sharepoint" in out and "PAUSED" in out
    assert "search_requests" in out and "91.0%" in out
    assert "pi_and_jailbreak" in out


def test_render_top_unavailable_panels():
    data = _sample_data()
    data.update(stats=None, quota=None, armor=None, connectors=None)
    data["errors"] = {"stats": "PermissionDenied: nope"}
    console = Console(file=StringIO(), width=150, force_terminal=False)
    console.print(top._render_top(data, interval=None))
    out = console.file.getvalue()
    assert out.count("unavailable") == 4
    assert "PermissionDenied" in out
