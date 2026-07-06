"""getop config — app-level configuration and feature toggles (read-only).

Surfaces the settings an admin sees on an app's console Configurations pages
(Observability, Feature Management, Knowledge Graph, agent gallery
visibility) from the v1alpha Engine resource, fetched via Clients.rest_get
(HTTP GET only — the published client's Engine proto doesn't carry these
fields).

Tri-state semantics for the config booleans: when the parent config object
(e.g. observabilityConfig) is absent from the response the value is None
("not returned" — older apps or API drift), rendered as a dim em dash and
never conflated with "off". When the parent is present but the bool key is
omitted the value is False, because proto3 REST omits false booleans.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests
import typer
from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from getop import render
from getop.auth import Clients, get_clients
from getop.commands import ls as ls_cmd

# ---- feature-map helpers (shared with `info`) --------------------------------


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


# ---- collection (pure parse + REST fetch) ------------------------------------


def _tri(parent: Any, key: str) -> bool | None:
    """Tri-state bool from an optional config object (see module docstring)."""
    if not isinstance(parent, dict):
        return None
    return bool(parent.get(key, False))


def parse_engine_config(data: dict) -> dict:
    """Flatten the config surfaces of a v1alpha Engine GET body. JSON-safe."""
    common = data.get("commonConfig") or {}
    features = data.get("features") or {}
    return {
        "app_type": data.get("appType"),
        "observability_enabled": _tri(
            data.get("observabilityConfig"), "observabilityEnabled"
        ),
        "sensitive_logging_enabled": _tri(
            data.get("observabilityConfig"), "sensitiveLoggingEnabled"
        ),
        "cloud_knowledge_graph_enabled": _tri(
            data.get("knowledgeGraphConfig"), "enableCloudKnowledgeGraph"
        ),
        "private_knowledge_graph_enabled": _tri(
            data.get("knowledgeGraphConfig"), "enablePrivateKnowledgeGraph"
        ),
        "marketplace_agent_visibility": data.get("marketplaceAgentVisibility"),
        "analytics_disabled": data.get("disableAnalytics"),
        "company_name": common.get("companyName"),
        "kms_key": (common.get("kmsConfig") or {}).get("kmsKey"),
        "features": features,
        "features_normalized": normalize_features(features),
    }


def fetch_engine_config(clients: Clients, engine_name: str) -> dict:
    """GET the v1alpha Engine and return its flattened config fields."""
    return parse_engine_config(clients.rest_get(f"v1alpha/{engine_name}"))


def collect_engine_configs(clients: Clients, engine_id: str | None = None) -> dict:
    """Config for one app (direct GET) or every app under the collection.

    A per-engine fetch failure in the all-apps path is isolated: the engine
    keeps an "error" entry and the others are unaffected. The single-app
    path lets HTTPError propagate so the command can report 404 cleanly.
    """
    errors: dict[str, str] = {}

    if engine_id:
        name = f"{clients.collection_path}/engines/{engine_id}"
        data = clients.rest_get(f"v1alpha/{name}")
        engines = [
            {
                "id": engine_id,
                "name": data.get("name") or name,
                "display_name": data.get("displayName"),
                "create_time": data.get("createTime"),
                **parse_engine_config(data),
            }
        ]
    else:
        engines = ls_cmd.collect_engines(clients)

        def fetch(engine: dict) -> None:
            try:
                engine.update(fetch_engine_config(clients, engine["name"]))
            except Exception as exc:  # noqa: BLE001 - isolate per-engine failure
                engine["error"] = f"{type(exc).__name__}: {exc}"
                errors[engine["id"]] = engine["error"]

        if engines:
            with ThreadPoolExecutor(max_workers=min(8, len(engines))) as pool:
                list(pool.map(fetch, engines))

    return {
        "project": clients.project,
        "location": clients.location,
        "engines": engines,
        "errors": errors,
    }


# ---- rendering ----------------------------------------------------------------


def _mark(value: bool | None) -> str:
    """✓ on / ✗ off / — (unknown) cell markup for a tri-state bool."""
    if value is None:
        return "[dim]—[/dim]"
    return "[green]✓ on[/green]" if value else "[dim]✗ off[/dim]"


def _prompt_logging_mark(value: bool | None) -> str:
    """Prompt/response logging ON is the flag-worthy state: bold red."""
    return "[bold red]⚠ ON[/bold red]" if value else _mark(value)


def _kg_mark(cloud: bool | None, private: bool | None) -> str:
    """Collapse the two knowledge-graph bools into one cell."""
    if cloud is None and private is None:
        return "[dim]—[/dim]"
    enabled = [label for label, on in (("cloud", cloud), ("private", private)) if on]
    return "+".join(enabled) if enabled else "[dim]✗ off[/dim]"


def _app_type(engine: dict) -> str:
    return (engine.get("app_type") or "").removeprefix("APP_TYPE_").lower()


def _render_table(data: dict) -> Any:
    title = f"App configuration — {data['project']} ({data['location']})"
    columns = [
        "App",
        "Type",
        "Observability",
        "Prompt logging",
        "Knowledge graph",
        "Agent gallery",
        "Analytics",
    ]
    rows: list[list[str]] = []
    for engine in data["engines"]:
        app_cell = f"{engine.get('display_name') or engine.get('id')}\n[dim]{engine.get('id')}[/dim]"
        if engine.get("error"):
            rows.append(
                [app_cell, _app_type(engine), "[yellow]fetch failed[/yellow]", "", "", "", ""]
            )
            continue
        rows.append(
            [
                app_cell,
                _app_type(engine) or "[dim]—[/dim]",
                _mark(engine.get("observability_enabled")),
                _prompt_logging_mark(engine.get("sensitive_logging_enabled")),
                _kg_mark(
                    engine.get("cloud_knowledge_graph_enabled"),
                    engine.get("private_knowledge_graph_enabled"),
                ),
                engine.get("marketplace_agent_visibility") or "[dim]—[/dim]",
                _mark(not engine.get("analytics_disabled")),
            ]
        )
    return render.table(title, columns, rows)


def _render_card(engine: dict) -> Panel:
    lines: list[Text] = []

    created = (engine.get("create_time") or "")[:10]
    header_bits = [
        bit
        for bit in (
            _app_type(engine),
            f"created {created}" if created else None,
            engine.get("company_name"),
        )
        if bit
    ]
    lines.append(Text.from_markup(f"[dim]{' · '.join(header_bits)}[/dim]"))

    lines.append(Text.from_markup("[bold]Observability[/bold]"))
    lines.append(
        Text.from_markup(
            f"  Traces/logs export: {_mark(engine.get('observability_enabled'))}"
        )
    )
    sensitive = engine.get("sensitive_logging_enabled")
    if sensitive:
        lines.append(
            Text.from_markup(
                "  Prompt/response logging: [bold red]⚠ ON[/bold red] — "
                "conversation content is retained in logs (see `getop logs user`)"
            )
        )
    else:
        lines.append(
            Text.from_markup(f"  Prompt/response logging: {_prompt_logging_mark(sensitive)}")
        )

    lines.append(Text.from_markup("[bold]Knowledge graph[/bold]"))
    lines.append(
        Text.from_markup(
            f"  Cloud: {_mark(engine.get('cloud_knowledge_graph_enabled'))} · "
            f"Private: {_mark(engine.get('private_knowledge_graph_enabled'))}"
        )
    )

    lines.append(Text.from_markup("[bold]Publishing[/bold]"))
    lines.append(
        Text.from_markup(
            "  Agent gallery visibility: "
            f"{engine.get('marketplace_agent_visibility') or '[dim]—[/dim]'}"
        )
    )

    lines.append(Text.from_markup("[bold]Data[/bold]"))
    cmek = engine.get("kms_key") or "[dim]— (Google-managed)[/dim]"
    lines.append(
        Text.from_markup(
            f"  Analytics: {_mark(not engine.get('analytics_disabled'))} · CMEK: {cmek}"
        )
    )

    features = engine.get("features_normalized") or {}
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


# ---- command -------------------------------------------------------------------


def config_command(
    ctx: typer.Context,
    engine: str = typer.Argument(
        None,
        help="App ID to inspect in detail. Omit to list every app's configuration.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Show app configuration: observability, prompt logging, feature toggles."""
    state = ctx.obj
    clients = get_clients(state.project, state.location, getattr(state, "quota_project", None))

    try:
        data = collect_engine_configs(clients, engine)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if engine and status == 404:
            render.err_console.print(
                f"[bold red]Error:[/bold red] app {engine!r} not found "
                f"(project {clients.project}, location {clients.location}). "
                "Run [bold]getop ls engines[/bold] to list app IDs."
            )
            raise typer.Exit(code=1) from None
        raise

    if engine:
        rendered: Any = _render_card(data["engines"][0])
    elif data["engines"]:
        rendered = _render_table(data)
    else:
        rendered = Text("No engines in default_collection.", style="yellow")

    render.output(data, rendered, as_json)

    if not as_json:
        for engine_id, message in (data.get("errors") or {}).items():
            render.err_console.print(f"[yellow]{engine_id}: {message}[/yellow]")
        if not engine and data["engines"]:
            render.err_console.print(
                "[dim]Run 'getop config <app-id>' for the full feature list.[/dim]"
            )
