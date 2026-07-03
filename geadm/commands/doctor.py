"""geadm doctor — composite read-only health check for Gemini Enterprise.

Reuses the collect functions from ls/logs/stats; issues only list/get reads.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import typer

from geadm import duration, render
from geadm.auth import Clients, get_clients
from geadm.commands import logs as logs_cmd
from geadm.commands import ls as ls_cmd
from geadm.commands import stats as stats_cmd

OK, WARN, FAIL = "OK", "WARN", "FAIL"
_STATUS_STYLE = {OK: "bold green", WARN: "bold yellow", FAIL: "bold red"}


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


def run_doctor(clients: Clients, since: str) -> list[dict]:
    """Execute all health checks and return JSON-safe result rows."""
    results: list[dict] = []
    engines: list[dict] = []

    def check_engines() -> tuple[str, str]:
        nonlocal engines
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
        return OK, f"{len(real)} agent(s) across {len(engines)} engine(s)"

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
        filter_str = (
            'resource.type="consumed_api" '
            'resource.labels.service="discoveryengine.googleapis.com" '
            "severity>=ERROR "
            f'timestamp>="{duration.since_rfc3339(since)}"'
        )
        entries = logs_cmd.collect_entries(clients, filter_str, limit=20)
        if entries:
            return WARN, f"{len(entries)} API ERROR log(s) since {since}"
        return OK, f"no discoveryengine API ERROR logs since {since}"

    def check_metrics() -> tuple[str, str]:
        data = stats_cmd.collect_stats(clients, since=since)
        discovered = data.get("metrics_discovered") or []
        if not discovered:
            return WARN, "no discoveryengine.googleapis.com metrics discovered"
        freshest = (data.get("connector") or {}).get("freshest_point")
        note = f", freshest connector point {freshest}" if freshest else ""
        return OK, f"{len(discovered)} metric type(s) discovered{note}"

    results.append(_check("engines", check_engines))
    results.append(_check("datastores", check_datastores))
    results.append(_check("data connectors", check_connectors))
    results.append(_check("agents", check_agents))
    results.append(_check(f"connector errors ({since})", check_connector_errors))
    results.append(_check(f"API errors ({since})", check_api_errors))
    results.append(_check("monitoring metrics", check_metrics))
    return results


def doctor_command(
    ctx: typer.Context,
    since: str = typer.Option(
        "24h", "--since", help="Window for log/metric checks (e.g. 1h, 24h, 7d)."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Composite read-only health check across engines, data stores, the data
    connector, agents, connector/API error logs and monitoring metrics.

    Needs only the viewer roles (roles/discoveryengine.viewer,
    roles/logging.viewer, roles/monitoring.viewer); performs no writes.
    Exits 1 if any check FAILs.
    """
    state = ctx.obj
    clients = get_clients(state.project, state.location)
    results = run_doctor(clients, since)

    def styled(row: dict) -> list[Any]:
        from rich.text import Text

        return [
            row["check"],
            Text(row["status"], style=_STATUS_STYLE.get(row["status"], "white")),
            row["detail"],
        ]

    renderable = render.table(
        f"geadm doctor — {clients.project} ({clients.location})",
        ["Check", "Status", "Detail"],
        [styled(r) for r in results],
    )
    render.output(results, renderable, as_json)
    if any(r["status"] == FAIL for r in results):
        raise typer.Exit(code=1)
