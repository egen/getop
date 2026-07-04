"""getop doctor — composite read-only health check for Gemini Enterprise.

Reuses the collect functions from ls/logs/stats; issues only list/get reads.
Checks run concurrently and the results table renders live, with a spinner
on each row until that check resolves.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import typer
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

from getop import duration, render
from getop.auth import Clients, get_clients
from getop.commands import logs as logs_cmd
from getop.commands import ls as ls_cmd
from getop.commands import stats as stats_cmd

OK, WARN, FAIL = "OK", "WARN", "FAIL"
_STATUS_STYLE = {OK: "bold green", WARN: "bold yellow", FAIL: "bold red"}

Check = tuple[str, Callable[[], tuple[str, str]]]


def _check(name: str, fn: Callable[[], tuple[str, str]]) -> dict:
    """Run one check, converting exceptions into FAIL rows so the remaining
    checks still run."""
    try:
        status, detail = fn()
    except Exception as exc:  # noqa: BLE001 - doctor must survive any check
        status, detail = FAIL, f"{type(exc).__name__}: {exc}"
    return {"check": name, "status": status, "detail": detail}


def _sync_age(last_sync_time: str | None) -> timedelta | None:
    if not last_sync_time:
        return None
    try:
        ts = datetime.fromisoformat(last_sync_time.replace("Z", "+00:00"))
    except ValueError:
        return None
    return datetime.now(timezone.utc) - ts


def _build_checks(clients: Clients, since: str) -> list[Check]:
    """Independent health checks, safe to run concurrently."""

    def check_engines() -> tuple[str, str]:
        engines = ls_cmd.collect_engines(clients)
        if not engines:
            return WARN, "no engines in default_collection"
        names = ", ".join(e.get("id") or "?" for e in engines[:5])
        return OK, f"{len(engines)} engine(s): {names}"

    def check_datastores() -> tuple[str, str]:
        stores = ls_cmd.collect_datastores(clients)
        if not stores:
            return WARN, "no data stores in default_collection"
        return OK, f"{len(stores)} data store(s)"

    def check_connectors() -> tuple[str, str]:
        connectors = ls_cmd.collect_connectors(clients)
        if not connectors:
            return WARN, "no data connectors configured in any collection"
        problems: list[str] = []
        for connector in connectors:
            source = connector.get("data_source") or connector.get("collection") or "?"
            state = connector.get("state") or "UNKNOWN"
            age = _sync_age(connector.get("last_sync_time"))
            if state not in ("ACTIVE", "STATE_ACTIVE"):
                problems.append(f"{source} state={state}")
            elif age is not None and age > timedelta(hours=24):
                problems.append(
                    f"{source} last sync {age.days}d{age.seconds // 3600}h ago (stale?)"
                )
        if problems:
            return WARN, f"{len(connectors)} connector(s); " + "; ".join(problems)
        return OK, f"{len(connectors)} connector(s), all ACTIVE and synced <24h"

    def check_agents() -> tuple[str, str]:
        agents = ls_cmd.collect_agents(clients)
        real = [a for a in agents if not a.get("note")]
        engine_count = len({a.get("engine_id") for a in agents if a.get("engine_id")})
        return OK, f"{len(real)} agent(s) across {engine_count} engine(s)"

    def check_connector_errors() -> tuple[str, str]:
        filter_str = logs_cmd.connector_filter(
            clients.project, datastore=None, severity="ERROR", since=since
        )
        entries = logs_cmd.collect_entries(clients, filter_str, limit=20)
        if entries:
            newest = entries[0].get("timestamp") or "?"
            return FAIL, f"{len(entries)} connector ERROR log(s) since {since} (newest {newest})"
        return OK, f"no connector ERROR logs since {since}"

    def check_api_errors() -> tuple[str, str]:
        # consumed_api's service label carries the gRPC service name
        # (e.g. google.cloud.discoveryengine.v1main.AssistantService), so
        # match by substring rather than the API hostname.
        filter_str = (
            'resource.type="consumed_api" '
            'resource.labels.service:"discoveryengine" '
            "severity>=ERROR "
            f'timestamp>="{duration.since_rfc3339(since)}"'
        )
        entries = logs_cmd.collect_entries(clients, filter_str, limit=20)
        if entries:
            return WARN, f"{len(entries)} API ERROR log(s) since {since}"
        return OK, f"no discoveryengine API ERROR logs since {since}"

    def check_metrics() -> tuple[str, str]:
        # Time-series values are only fetched for connector metrics; querying
        # every discovered metric type would take minutes on busy projects.
        data = stats_cmd.collect_stats(clients, since=since, categories={"connector"})
        discovered = data.get("metrics_discovered") or []
        if not discovered:
            return WARN, "no discoveryengine.googleapis.com metrics discovered"
        freshest = (data.get("connector") or {}).get("freshest_point")
        note = f", freshest connector point {freshest}" if freshest else ""
        return OK, f"{len(discovered)} metric type(s) discovered{note}"

    return [
        ("engines", check_engines),
        ("datastores", check_datastores),
        ("data connectors", check_connectors),
        ("agents", check_agents),
        (f"connector errors ({since})", check_connector_errors),
        (f"API errors ({since})", check_api_errors),
        ("monitoring metrics", check_metrics),
    ]


def run_doctor(clients: Clients, since: str) -> list[dict]:
    """Execute all health checks concurrently and return JSON-safe rows."""
    checks = _build_checks(clients, since)
    results: list[dict | None] = [None] * len(checks)
    with ThreadPoolExecutor(max_workers=len(checks)) as pool:
        futures = {
            pool.submit(_check, name, fn): i for i, (name, fn) in enumerate(checks)
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return [r for r in results if r is not None]


def _result_table(title: str, checks: list[Check], results: list[dict | None]) -> Any:
    rows: list[list[Any]] = []
    for i, (name, _fn) in enumerate(checks):
        result = results[i]
        if result is None:
            rows.append([name, Spinner("dots", style="cyan"), Text("checking…", style="dim")])
        else:
            rows.append(
                [
                    result["check"],
                    Text(result["status"], style=_STATUS_STYLE.get(result["status"], "white")),
                    result["detail"],
                ]
            )
    return render.table(title, ["Check", "Status", "Detail"], rows)


def doctor_command(
    ctx: typer.Context,
    since: str = typer.Option(
        "24h", "--since", help="Window for log/metric checks (e.g. 1h, 24h, 7d)."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Run a health check (exits non-zero on failure)."""
    state = ctx.obj
    clients = get_clients(state.project, state.location, getattr(state, "quota_project", None))

    if as_json:
        results = run_doctor(clients, since)
        render.emit_json(results)
        if any(r["status"] == FAIL for r in results):
            raise typer.Exit(code=1)
        return

    checks = _build_checks(clients, since)
    results: list[dict | None] = [None] * len(checks)
    title = f"getop doctor — {clients.project} ({clients.location})"

    with Live(
        _result_table(title, checks, results),
        console=render.console,
        refresh_per_second=10,
    ) as live:
        with ThreadPoolExecutor(max_workers=len(checks)) as pool:
            futures = {
                pool.submit(_check, name, fn): i for i, (name, fn) in enumerate(checks)
            }
            for future in as_completed(futures):
                results[futures[future]] = future.result()
                live.update(_result_table(title, checks, results))

    if any(r is not None and r["status"] == FAIL for r in results):
        raise typer.Exit(code=1)
