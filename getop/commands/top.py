"""getop top — the live dashboard the project name promised.

A full-screen, auto-refreshing view (like `top`) composing the existing
read-only collectors: active users derived from license last-logins,
query volume / latency and quota from Cloud Monitoring, connector health,
and Model Armor hits from Cloud Logging. No new API surface — every panel
reuses a collector another command already owns.

Active-user semantics: Gemini Enterprise has no active-user metric, but
UserLicense.lastLoginTime updates on login, so anyone active inside a
trailing window necessarily has their last login inside it. Counting
licenses with lastLoginTime >= now-24h/7d/30d therefore gives exact
trailing DAU/WAU/MAU *as of now*. Historical curves are not reconstructable
this way (each login overwrites the previous timestamp); true per-day
history needs the user-activity log, which requires observability logging.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

import typer
from rich.columns import Columns
from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from getop import render
from getop.auth import Clients, get_clients
from getop.commands import armor as armor_cmd
from getop.commands import ls as ls_cmd
from getop.commands import stats as stats_cmd

# ---- active users from license last-logins ------------------------------------

_WINDOWS = (("dau", timedelta(days=1)), ("wau", timedelta(days=7)), ("mau", timedelta(days=30)))


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    # proto timestamps arrive tz-aware; treat a naive one as UTC rather
    # than crashing the comparison.
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def active_users(licenses: list[dict], now: datetime | None = None) -> dict:
    """Trailing 24h/7d/30d active-user counts from license last-logins."""
    now = now or datetime.now(timezone.utc)
    logins = [ts for l in licenses if (ts := _parse_ts(l.get("last_login_time")))]
    counts = {
        key: sum(1 for ts in logins if ts >= now - delta) for key, delta in _WINDOWS
    }
    assigned = sum(
        1 for l in licenses if (l.get("license_assignment_state") or "") == "ASSIGNED"
    )
    return {
        **counts,
        "assigned": assigned,
        "licensed_users": len(licenses),
        "never_logged_in": sum(1 for l in licenses if not l.get("last_login_time")),
    }


# ---- collection ---------------------------------------------------------------


def collect_top(clients: Clients, since: str) -> dict:
    """One dashboard refresh: run every panel's collector concurrently.

    Each panel is isolated — a failing source lands in `errors` and its
    panel renders as unavailable instead of killing the refresh.
    """
    jobs: dict[str, Any] = {
        "licenses": lambda: ls_cmd.collect_licenses(clients),
        "connectors": lambda: ls_cmd.collect_connectors(clients),
        "stats": lambda: stats_cmd.collect_stats(
            clients, since, categories={"query_volume", "latency"}
        ),
        "quota": lambda: stats_cmd.collect_quota(clients, since),
        "armor": lambda: armor_cmd.summarise(
            armor_cmd.collect_violations(
                clients,
                armor_cmd.armor_filter(clients.project, since, matched_only=True),
                limit=500,
            )
        ),
    }
    results: dict[str, Any] = {}
    errors: dict[str, str] = {}

    def run(item: tuple[str, Any]) -> None:
        name, fn = item
        try:
            results[name] = fn()
        except Exception as exc:  # noqa: BLE001 - a partial dashboard beats none
            results[name] = None
            errors[name] = f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        list(pool.map(run, jobs.items()))

    return {
        "project": clients.project,
        "location": clients.location,
        "since": since,
        "refreshed": datetime.now(timezone.utc).isoformat(),
        "active_users": active_users(results.get("licenses") or []),
        "connectors": results.get("connectors"),
        "stats": results.get("stats"),
        "quota": results.get("quota"),
        "armor": results.get("armor"),
        "errors": errors,
    }


# ---- rendering ----------------------------------------------------------------


def _tile(value: str, label: str, style: str = "bold cyan") -> Panel:
    return Panel(
        Text(value, style=style, justify="center"),
        title=label,
        title_align="center",
        border_style="blue",
        width=22,
    )


def _users_row(users: dict) -> Columns:
    seats = f"{users['assigned']} assigned"
    return Columns(
        [
            _tile(str(users["dau"]), "Active 24h"),
            _tile(str(users["wau"]), "Active 7d"),
            _tile(str(users["mau"]), "Active 30d"),
            _tile(seats, "Licenses", style="bold white"),
        ],
        equal=False,
        expand=False,
    )


def _metric_short(metric_type: str) -> str:
    return metric_type.rsplit("/", 1)[-1]


def _stats_panel(stats: dict | None, since: str) -> Panel:
    lines: list[Text] = []
    if stats is None:
        lines.append(Text("unavailable", style="yellow"))
    else:
        buckets = (("query volume", stats["query_volume"]), ("latency", stats["latency"]))
        for label, bucket in buckets:
            for summary in bucket.values():
                agg = summary.get("aggregate")
                if agg is None:
                    continue  # metric discovered but no data points in window
                unit = " ms" if label == "latency" else ""
                lines.append(
                    Text.from_markup(
                        f"  {_metric_short(summary['type'])}: "
                        f"[bold]{stats_cmd._fmt_number(agg)}{unit}[/bold] "
                        f"[dim]({summary['aggregate_label']}, {since})[/dim]"
                    )
                )
        if not lines:
            lines.append(Text(f"no query traffic in the last {since}", style="dim"))
    return Panel(Group(*lines), title="Queries & latency", border_style="cyan")


def _age(iso: str | None, now: datetime) -> str:
    ts = _parse_ts(iso)
    if ts is None:
        # Absent lastSyncTime means the API didn't report one, not that a
        # sync never happened.
        return "unknown"
    seconds = int((now - ts).total_seconds())
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _connector_panel(connectors: list[dict] | None, now: datetime) -> Panel:
    lines: list[Text] = []
    if connectors is None:
        lines.append(Text("unavailable", style="yellow"))
    elif not connectors:
        lines.append(Text("no data connectors", style="dim"))
    else:
        for c in connectors:
            state = c.get("state") or "?"
            style = "green" if state == "ACTIVE" else "bold yellow"
            lines.append(
                Text.from_markup(
                    f"  [magenta]{c.get('data_source') or c.get('id')}[/magenta] "
                    f"[{style}]{state}[/{style}] "
                    f"[dim]synced {_age(c.get('last_sync_time'), now)}[/dim]"
                )
            )
    return Panel(Group(*lines), title="Connectors", border_style="cyan")


def _quota_panel(quota: list[dict] | None, max_rows: int = 5) -> Panel:
    lines: list[Any] = []
    if quota is None:
        lines.append(Text("unavailable", style="yellow"))
    elif not quota:
        lines.append(Text("no quota metrics with data", style="dim"))
    else:
        for row in quota[:max_rows]:
            pct = stats_cmd._pct_text(row.get("percent_used"))
            line = Text(f"  {row['quota']} ({row['location']}) ")
            line.append(pct)
            if row.get("exceeded"):
                line.append(f"  exceeded ×{row['exceeded']}", style="bold red")
            lines.append(line)
    return Panel(Group(*lines), title="Quota (most used)", border_style="cyan")


def _armor_panel(armor: list[dict] | None, since: str) -> Panel:
    lines: list[Text] = []
    if armor is None:
        lines.append(Text("unavailable", style="yellow"))
    elif not armor:
        lines.append(Text(f"no violations in {since}", style="green"))
    else:
        for row in armor[:5]:
            lines.append(
                Text.from_markup(
                    f"  {row['filter']}: [bold red]{row['hits']}[/bold red] "
                    f"[dim]last {(row.get('last_seen') or '?')[11:19]}[/dim]"
                )
            )
    return Panel(Group(*lines), title="Model Armor hits", border_style="cyan")


def _render_top(data: dict, interval: float | None) -> Group:
    now = datetime.now(timezone.utc)
    refreshed = datetime.now().strftime("%H:%M:%S")
    tail = f" · every {interval:.0f}s · Ctrl+C to quit" if interval else ""
    pieces: list[Any] = [
        Text.from_markup(
            f"[bold]getop top — {data['project']} ({data['location']})[/bold]"
            f" [dim]· window {data['since']} · refreshed {refreshed}{tail}[/dim]\n"
        ),
        _users_row(data["active_users"]),
        Text(""),
        Columns(
            [
                _stats_panel(data.get("stats"), data["since"]),
                _connector_panel(data.get("connectors"), now),
            ],
            equal=False,
            expand=False,
        ),
        Columns(
            [
                _quota_panel(data.get("quota")),
                _armor_panel(data.get("armor"), data["since"]),
            ],
            equal=False,
            expand=False,
        ),
    ]
    for source, message in (data.get("errors") or {}).items():
        pieces.append(Text(f"{source}: {message}", style="yellow"))
    return Group(*pieces)


# ---- command -------------------------------------------------------------------


def top_command(
    ctx: typer.Context,
    interval: float = typer.Option(
        30.0,
        "--interval",
        "-n",
        help="Seconds between refreshes (minimum 10).",
    ),
    since: str = typer.Option(
        "1h",
        "--since",
        help="Metric/log window per refresh, e.g. 30m, 1h, 24h.",
    ),
    once: bool = typer.Option(
        False, "--once", help="Render a single snapshot and exit."
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit one machine-readable JSON snapshot."
    ),
) -> None:
    """Live dashboard: active users, queries, connectors, quota, Model Armor."""
    state = ctx.obj
    clients = get_clients(state.project, state.location, getattr(state, "quota_project", None))

    if as_json or once:
        data = collect_top(clients, since)
        render.output(data, _render_top(data, interval=None), as_json)
        return

    import time as _time

    from rich.live import Live

    interval = max(interval, 10.0)
    data = collect_top(clients, since)
    try:
        with Live(
            _render_top(data, interval),
            console=render.console,
            screen=True,
            refresh_per_second=4,
        ) as live:
            while True:
                _time.sleep(interval)
                data = collect_top(clients, since)
                live.update(_render_top(data, interval))
    except KeyboardInterrupt:
        raise typer.Exit(code=0) from None
