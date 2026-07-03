"""geadm info — project-wide Gemini Enterprise overview with per-engine cards.

Composite read-only view built from the ls collectors (engines, data stores,
connectors, agents, licenses), gathered concurrently like doctor.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import typer
from rich.columns import Columns
from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from geadm import render
from geadm.auth import Clients, get_clients
from geadm.commands import ls as ls_cmd


def collect_info(clients: Clients) -> dict:
    """Gather the full project inventory concurrently. JSON-safe."""
    jobs: dict[str, Any] = {
        "engines": ls_cmd.collect_engines,
        "datastores": ls_cmd.collect_datastores,
        "connectors": ls_cmd.collect_connectors,
        "agents": ls_cmd.collect_agents,
        "licenses": ls_cmd.collect_licenses,
    }
    results: dict[str, Any] = {}
    errors: dict[str, str] = {}

    def run(item: tuple[str, Any]) -> None:
        name, fn = item
        try:
            results[name] = fn(clients)
        except Exception as exc:  # noqa: BLE001 - partial info beats none
            results[name] = []
            errors[name] = f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        list(pool.map(run, jobs.items()))

    results["license_configs"] = _collect_license_configs(
        clients, results.get("licenses") or [], errors
    )
    _attach_engine_details(clients, results.get("engines") or [], errors)

    return {
        "project": clients.project,
        "location": clients.location,
        **results,
        "errors": errors,
    }


def _collect_license_configs(
    clients: Clients, licenses: list[dict], errors: dict[str, str]
) -> list[dict]:
    """Fetch each distinct licenseConfig referenced by the user licenses
    (v1 REST GET) for seat counts and subscription details."""
    names = sorted({l.get("license_config") for l in licenses if l.get("license_config")})
    configs: list[dict] = []
    for name in names:
        try:
            data = clients.rest_get(f"v1/{name}")
        except Exception as exc:  # noqa: BLE001 - seats are a nice-to-have
            errors["license_configs"] = f"{type(exc).__name__}: {exc}"
            continue
        configs.append(
            {
                "name": data.get("name"),
                "license_count": int(data.get("licenseCount") or 0),
                "subscription_tier": data.get("subscriptionTier"),
                "state": data.get("state"),
                "auto_renew": data.get("autoRenew"),
                "start_date": data.get("startDate"),
                "end_date": data.get("endDate"),
            }
        )
    return configs


def _attach_engine_details(
    clients: Clients, engines: list[dict], errors: dict[str, str]
) -> None:
    """Enrich engine dicts with the v1alpha-only detail fields (features map,
    app type) that the published client's Engine proto doesn't carry."""

    def fetch(engine: dict) -> None:
        try:
            data = clients.rest_get(f"v1alpha/{engine['name']}")
        except Exception as exc:  # noqa: BLE001 - details are a nice-to-have
            errors["engine_details"] = f"{type(exc).__name__}: {exc}"
            return
        engine["features"] = data.get("features") or {}
        engine["app_type"] = data.get("appType")
        engine["marketplace_agent_visibility"] = data.get("marketplaceAgentVisibility")

    if engines:
        with ThreadPoolExecutor(max_workers=min(8, len(engines))) as pool:
            list(pool.map(fetch, engines))


def normalize_features(raw: dict[str, str]) -> dict[str, bool]:
    """Feature map -> capability name -> enabled.

    GE encodes some toggles inverted (`disable-X = FEATURE_STATE_ON` means X
    is off); strip the prefix and flip so every entry reads as a capability.
    """
    normalized: dict[str, bool] = {}
    for key, value in (raw or {}).items():
        on = value == "FEATURE_STATE_ON"
        if key.startswith("disable-"):
            normalized[key.removeprefix("disable-")] = not on
        else:
            normalized[key] = on
    return normalized


def _wrap_names(names: list[str], prefix: str, width: int = 52) -> list[str]:
    """Chunk feature names into indented lines so cards stay compact."""
    lines: list[str] = []
    current = ""
    for name in names:
        candidate = f"{current} {name}".strip()
        if current and len(candidate) > width:
            lines.append(current)
            current = name
        else:
            current = candidate
    if current:
        lines.append(current)
    return [f"{prefix}{line}" for line in lines]


def _connector_by_datastore(connectors: list[dict]) -> dict[str, dict]:
    """Map data store ID -> owning connector, via connector entities."""
    mapping: dict[str, dict] = {}
    for connector in connectors:
        for entity in connector.get("entities") or []:
            data_store = entity.get("data_store") or ""
            ds_id = data_store.rstrip("/").rsplit("/", 1)[-1]
            if ds_id:
                mapping[ds_id] = connector
    return mapping


def _summary_tiles(data: dict) -> Columns:
    connectors = data["connectors"]
    active = sum(1 for c in connectors if (c.get("state") or "") == "ACTIVE")
    licenses = data["licenses"]
    assigned = sum(
        1 for l in licenses if (l.get("license_assignment_state") or "") == "ASSIGNED"
    )
    used = sum(
        1
        for l in licenses
        if (l.get("license_assignment_state") or "") == "ASSIGNED"
        and l.get("last_login_time")
    )
    awaiting = sum(
        1
        for l in licenses
        if (l.get("license_assignment_state") or "") == "NO_LICENSE_ATTEMPTED_LOGIN"
    )
    seats = sum(
        c.get("license_count") or 0
        for c in data.get("license_configs") or []
        if (c.get("state") or "") == "ACTIVE"
    )
    agents = [a for a in data["agents"] if not a.get("note")]

    def tile(value: Any, label: str, style: str = "bold cyan") -> Panel:
        body = value if isinstance(value, (Text, Group)) else Text(
            str(value), style=style, justify="center"
        )
        return Panel(
            body,
            title=label,
            title_align="center",
            border_style="blue",
            width=24,
        )

    if seats:
        pct = 100.0 * assigned / seats
        pct_style = "bold red" if pct >= 90 else "bold yellow" if pct >= 75 else "bold cyan"
        headline = Text(f"{assigned}/{seats} ({pct:.1f}%)", style=pct_style, justify="center")
    else:
        headline = Text(f"{assigned} assigned", style="bold cyan", justify="center")
    license_lines: list[Text] = [
        headline,
        Text(f"{used}/{assigned} logged in", style="dim", justify="center"),
    ]
    if awaiting:
        license_lines.append(
            Text(f"{awaiting} awaiting license", style="yellow", justify="center")
        )
    license_body = Group(*license_lines)

    connector_style = "bold green" if active == len(connectors) else "bold yellow"
    return Columns(
        [
            tile(str(len(data["engines"])), "Engines"),
            tile(str(len(data["datastores"])), "Data stores"),
            tile(
                f"{active}/{len(connectors)} ACTIVE" if connectors else "0",
                "Connectors",
                connector_style,
            ),
            tile(str(len(agents)), "Agents"),
            tile(license_body, "Licenses"),
        ],
        equal=False,
        expand=False,
    )


# Every user gets an auto-created private "My Agent"; with adoption there will
# be hundreds per engine, so they are always summarised as one grouped line.
_DEFAULT_AGENT_NAME = "My Agent"

_MAX_DATASTORE_ROWS = 10
_MAX_AGENT_ROWS = 8


def _agent_lines(agents: list[dict], max_rows: int = _MAX_AGENT_ROWS) -> list[str]:
    """Markup lines summarising an engine's agents.

    "My Agent" user defaults collapse to a single ×count line; remaining
    agents are grouped by (name, state), ENABLED first, truncated to
    max_rows with a "+N more" tail.
    """
    from collections import Counter

    defaults = [a for a in agents if (a.get("display_name") or "") == _DEFAULT_AGENT_NAME]
    named = [a for a in agents if (a.get("display_name") or "") != _DEFAULT_AGENT_NAME]

    counts = Counter(
        (a.get("display_name") or a.get("id") or "?", a.get("state") or "")
        for a in named
    )
    ordered = sorted(
        counts.items(),
        key=lambda kv: (kv[0][1] != "ENABLED", kv[0][0].lower()),
    )

    lines: list[str] = []
    for (name, state), count in ordered[:max_rows]:
        times = f" [dim]×{count}[/dim]" if count > 1 else ""
        state_note = f" [dim]{state}[/dim]" if state else ""
        lines.append(f"  • {name}{times}{state_note}")
    hidden = sum(count for _, count in ordered[max_rows:])
    if hidden:
        lines.append(f"  [dim]… +{hidden} more[/dim]")
    if defaults:
        lines.append(
            f"  • {_DEFAULT_AGENT_NAME} [dim]×{len(defaults)} (user defaults, private)[/dim]"
        )
    if not lines:
        lines.append("  [dim]none[/dim]")
    return lines


def _engine_card(engine: dict, data: dict, ds_to_connector: dict[str, dict]) -> Panel:
    lines: list[Text] = []
    app_type = (engine.get("app_type") or "").removeprefix("APP_TYPE_").lower()
    app_note = f" · {app_type}" if app_type else ""
    lines.append(
        Text.from_markup(
            f"[dim]{(engine.get('solution_type') or '?').removeprefix('SOLUTION_TYPE_')}"
            f" · {engine.get('industry_vertical') or '?'}{app_note}"
            f" · created {(engine.get('create_time') or '?')[:10]}[/dim]"
        )
    )

    ds_ids = engine.get("data_store_ids") or []
    lines.append(Text.from_markup(f"[bold]Data stores ({len(ds_ids)})[/bold]"))
    for ds_id in ds_ids[:_MAX_DATASTORE_ROWS]:
        connector = ds_to_connector.get(ds_id)
        if connector:
            state = connector.get("state") or "?"
            # ACTIVE is the healthy default — only call out other states.
            state_note = "" if state == "ACTIVE" else f" [yellow]{state}[/yellow]"
            source = (
                f" [dim]←[/dim] [magenta]{connector.get('data_source') or '?'}[/magenta]"
                f"{state_note}"
            )
        else:
            source = ""
        lines.append(Text.from_markup(f"  • {ds_id}{source}"))
    if len(ds_ids) > _MAX_DATASTORE_ROWS:
        lines.append(
            Text.from_markup(f"  [dim]… +{len(ds_ids) - _MAX_DATASTORE_ROWS} more[/dim]")
        )

    agents = [
        a
        for a in data["agents"]
        if a.get("engine_id") == engine.get("id") and not a.get("note")
    ]
    enabled = sum(1 for a in agents if (a.get("state") or "") == "ENABLED")
    defaults = sum(
        1 for a in agents if (a.get("display_name") or "") == _DEFAULT_AGENT_NAME
    )
    breakdown = f"{len(agents)}"
    if agents:
        breakdown += f" — {enabled} enabled, {defaults} user defaults"
    lines.append(Text.from_markup(f"[bold]Agents ({breakdown})[/bold]"))
    lines.extend(Text.from_markup(line) for line in _agent_lines(agents))

    features = normalize_features(engine.get("features") or {})
    if features:
        on = sorted(name for name, enabled in features.items() if enabled)
        off = sorted(name for name, enabled in features.items() if not enabled)
        lines.append(
            Text.from_markup(f"[bold]Features ({len(on)} on · {len(off)} off)[/bold]")
        )
        for line in _wrap_names(on, "  "):
            lines.append(Text.from_markup(f"[green]✓[/green][dim]{line[1:]}[/dim]"))
        for line in _wrap_names(off, "  "):
            lines.append(Text.from_markup(f"[red]✗[/red][dim]{line[1:]}[/dim]"))

    return Panel(
        Group(*lines),
        title=f"[bold]{engine.get('display_name') or engine.get('id')}[/bold]",
        subtitle=f"[dim]{engine.get('id')}[/dim]",
        border_style="cyan",
    )


def _render_info(data: dict) -> Group:
    pieces: list[Any] = [
        Text.from_markup(
            f"[bold]Gemini Enterprise — {data['project']} ({data['location']})[/bold]\n"
        ),
        _summary_tiles(data),
        Text(""),
    ]

    ds_to_connector = _connector_by_datastore(data["connectors"])
    if data["engines"]:
        cards = [
            _engine_card(engine, data, ds_to_connector) for engine in data["engines"]
        ]
        pieces.append(Columns(cards, equal=False, expand=False))
    else:
        pieces.append(Text("No engines in default_collection.", style="yellow"))

    for check, message in (data.get("errors") or {}).items():
        pieces.append(Text(f"{check}: {message}", style="yellow"))
    return Group(*pieces)


def info_command(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Show a project overview."""
    state = ctx.obj
    clients = get_clients(state.project, state.location, getattr(state, "quota_project", None))

    if render.err_console.is_terminal and not as_json:
        with render.err_console.status("Collecting inventory…", spinner="dots"):
            data = collect_info(clients)
    else:
        data = collect_info(clients)

    render.output(data, _render_info(data), as_json)
