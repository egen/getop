"""geadm ls — list Gemini Enterprise resources (read-only).

Every command in this module walks the `default_collection` (or
`default_user_store`) hierarchy of a Discovery Engine project and prints what
it finds. Nothing here ever calls a mutating RPC (create/update/delete/import/
purge) — only list_*, get_* and Clients.rest_get (HTTP GET) are used. The only
IAM permission required is `roles/discoveryengine.viewer`.

Data collection is split from rendering so `geadm doctor` (and tests) can
call `collect_engines` / `collect_datastores` / `collect_connector` /
`collect_agents` / `collect_licenses` directly and get plain JSON-safe dicts
back.
"""

from __future__ import annotations

from typing import Any

import requests
import typer
from google.api_core import exceptions as gexceptions
from google.cloud import discoveryengine_v1

from geadm.auth import Clients, get_clients
from geadm.render import output, table

app = typer.Typer(
    help="List Gemini Enterprise resources (read-only, roles/discoveryengine.viewer).",
    no_args_is_help=True,
)


# ---- shared conversion helpers ----------------------------------------------


def _short_id(name: str | None) -> str:
    """Trailing path segment of a fully qualified resource name."""
    if not name:
        return ""
    return name.rstrip("/").rsplit("/", 1)[-1]


def _enum_name(value: Any) -> str | None:
    """Best-effort human-readable name for a protobuf enum value."""
    if value is None:
        return None
    name = getattr(value, "name", None)
    return name if name is not None else str(value)


def _iso(ts: Any) -> str | None:
    """ISO 8601 string for a protobuf Timestamp field (proto-plus returns
    None for unset well-known Timestamp fields, and a datetime-like object
    with .isoformat() when set).
    """
    if ts is None:
        return None
    isoformat = getattr(ts, "isoformat", None)
    if callable(isoformat):
        try:
            return isoformat()
        except Exception:
            pass
    return str(ts)


def _http_status(exc: requests.HTTPError) -> int | None:
    return exc.response.status_code if exc.response is not None else None


# ---- engines -----------------------------------------------------------------


def _engine_to_dict(engine: Any) -> dict:
    name = getattr(engine, "name", "") or ""
    return {
        "id": _short_id(name),
        "name": name,
        "display_name": getattr(engine, "display_name", None),
        "solution_type": _enum_name(getattr(engine, "solution_type", None)),
        "industry_vertical": _enum_name(getattr(engine, "industry_vertical", None)),
        "data_store_ids": list(getattr(engine, "data_store_ids", None) or []),
        "create_time": _iso(getattr(engine, "create_time", None)),
    }


def collect_engines(clients: Clients) -> list[dict]:
    """List every Engine (Search/Chat app) under the default collection."""
    client = clients.discoveryengine(discoveryengine_v1.EngineServiceClient)
    return [
        _engine_to_dict(engine)
        for engine in client.list_engines(parent=clients.collection_path)
    ]


# ---- data stores ---------------------------------------------------------------


def _datastore_to_dict(data_store: Any) -> dict:
    name = getattr(data_store, "name", "") or ""
    return {
        "id": _short_id(name),
        "name": name,
        "display_name": getattr(data_store, "display_name", None),
        "industry_vertical": _enum_name(getattr(data_store, "industry_vertical", None)),
        "content_config": _enum_name(getattr(data_store, "content_config", None)),
        "create_time": _iso(getattr(data_store, "create_time", None)),
    }


def collect_datastores(clients: Clients) -> list[dict]:
    """List every DataStore under the default collection."""
    client = clients.discoveryengine(discoveryengine_v1.DataStoreServiceClient)
    return [
        _datastore_to_dict(ds)
        for ds in client.list_data_stores(parent=clients.collection_path)
    ]


# ---- data connector (v1alpha REST, singleton per collection) -----------------


def _connector_from_rest(data: dict) -> dict:
    entities = [
        {"entity_name": ent.get("entityName"), "data_store": ent.get("dataStore")}
        for ent in (data.get("entities") or [])
    ]
    return {
        "id": _short_id(data.get("name")),
        "name": data.get("name"),
        "data_source": data.get("dataSource"),
        "state": data.get("state"),
        "refresh_interval": data.get("refreshInterval"),
        "last_sync_time": data.get("lastSyncTime"),
        "entities": entities,
    }


def collect_connector(clients: Clients, collection_name: str | None = None) -> dict | None:
    """Fetch one collection's singleton data connector, or None if there
    isn't one configured (HTTP 404). Any other error (e.g. 403) propagates
    to the caller so it can be surfaced with context.

    Defaults to default_collection; pass a full collection resource name to
    read another collection's connector.
    """
    collection = collection_name or clients.collection_path
    path = f"v1alpha/{collection}/dataConnector"
    try:
        data = clients.rest_get(path)
    except requests.HTTPError as exc:
        if _http_status(exc) == 404:
            return None
        raise
    return _connector_from_rest(data)


def collect_collections(clients: Clients) -> list[dict]:
    """List every collection in the location (v1alpha REST; each connector
    lives in its own collection, so default_collection alone is not enough)."""
    parent = f"projects/{clients.project}/locations/{clients.location}"
    collections: list[dict] = []
    page_token: str | None = None
    while True:
        params = {"pageSize": "100"}
        if page_token:
            params["pageToken"] = page_token
        data = clients.rest_get(f"v1alpha/{parent}/collections", params=params)
        for col in data.get("collections") or []:
            collections.append(
                {
                    "id": _short_id(col.get("name")),
                    "name": col.get("name"),
                    "display_name": col.get("displayName"),
                }
            )
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return collections


def collect_connectors(clients: Clients) -> list[dict]:
    """Fetch the data connector of every collection in the location.

    Connector-backed sources (Gmail, Drive, Jira, …) each create their own
    collection, so this scans all collections rather than just
    default_collection. Collections without a connector are skipped.
    """
    connectors: list[dict] = []
    for col in collect_collections(clients):
        connector = collect_connector(clients, col["name"])
        if connector is not None:
            connector["collection"] = col["id"]
            connector["collection_display_name"] = col["display_name"]
            connectors.append(connector)
    return connectors


# ---- agents (v1alpha REST, per-engine) ----------------------------------------


def _agent_to_dict(agent: dict, engine_id: str) -> dict:
    name = agent.get("name") or ""
    return {
        "engine_id": engine_id,
        "id": _short_id(name),
        "name": name,
        "display_name": agent.get("displayName"),
        "description": agent.get("description"),
        "state": agent.get("state"),
        "create_time": agent.get("createTime"),
    }


def _agent_error_note(status: int | None) -> str:
    if status == 404:
        return "no agents configured for this engine"
    if status == 403:
        return "permission denied listing agents for this engine"
    return "failed to list agents for this engine"


def collect_agents(clients: Clients) -> list[dict]:
    """List assistant agents for every engine under the default collection.

    Iterates `collect_engines` results to discover engine IDs (there is no
    top-level "list all agents" surface). A per-engine failure (404/403/etc.)
    is turned into a note row instead of aborting the whole command.
    """
    results: list[dict] = []
    for engine in collect_engines(clients):
        engine_id = engine["id"]
        path = (
            f"v1alpha/{clients.collection_path}/engines/{engine_id}"
            "/assistants/default_assistant/agents"
        )
        try:
            data = clients.rest_get(path)
        except requests.HTTPError as exc:
            status = _http_status(exc)
            results.append(
                {
                    "engine_id": engine_id,
                    "error": f"HTTP {status}" if status is not None else str(exc),
                    "note": _agent_error_note(status),
                }
            )
            continue
        agents = data.get("agents") or []
        if not agents:
            results.append({"engine_id": engine_id, "note": "no agents configured"})
        for agent in agents:
            results.append(_agent_to_dict(agent, engine_id))
    return results


# ---- user licenses (default_user_store) ---------------------------------------


def _license_to_dict(license_: Any) -> dict:
    return {
        "user_principal": getattr(license_, "user_principal", None),
        "user_profile": getattr(license_, "user_profile", None),
        "license_assignment_state": _enum_name(
            getattr(license_, "license_assignment_state", None)
        ),
        "license_config": getattr(license_, "license_config", None),
        "license_config_id": _short_id(getattr(license_, "license_config", None)),
        "create_time": _iso(getattr(license_, "create_time", None)),
        "update_time": _iso(getattr(license_, "update_time", None)),
        "last_login_time": _iso(getattr(license_, "last_login_time", None)),
    }


def collect_licenses(clients: Clients) -> list[dict]:
    """List every user license in the project's default user store.

    Reads projects/{project}/locations/{location}/userStores/default_user_store
    via UserLicenseServiceClient.list_user_licenses (read-only, pageable).
    """
    client = clients.discoveryengine(discoveryengine_v1.UserLicenseServiceClient)
    parent = (
        f"projects/{clients.project}/locations/{clients.location}"
        "/userStores/default_user_store"
    )
    return [
        _license_to_dict(license_) for license_ in client.list_user_licenses(parent=parent)
    ]


# ---- CLI commands --------------------------------------------------------------


@app.command()
def engines(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """List Search/Chat engines under the default collection.

    Read-only (EngineServiceClient.list_engines). Requires only
    roles/discoveryengine.viewer.
    """
    state = ctx.obj
    clients = get_clients(state.project, state.location, getattr(state, "quota_project", None))
    data = collect_engines(clients)
    rows = [
        (
            e["id"],
            e["display_name"],
            e["solution_type"],
            e["industry_vertical"],
            ", ".join(e["data_store_ids"]),
            e["create_time"],
        )
        for e in data
    ]
    rendered = table(
        "Engines",
        ["ID", "Display Name", "Solution Type", "Industry Vertical", "Data Stores", "Created"],
        rows,
    )
    output(data, rendered, as_json)


@app.command()
def datastores(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """List data stores under the default collection.

    Read-only (DataStoreServiceClient.list_data_stores). Requires only
    roles/discoveryengine.viewer.
    """
    state = ctx.obj
    clients = get_clients(state.project, state.location, getattr(state, "quota_project", None))
    data = collect_datastores(clients)
    rows = [
        (
            d["id"],
            d["display_name"],
            d["industry_vertical"],
            d["content_config"],
            d["create_time"],
        )
        for d in data
    ]
    rendered = table(
        "Data Stores",
        ["ID", "Display Name", "Industry Vertical", "Content Config", "Created"],
        rows,
    )
    output(data, rendered, as_json)


@app.command()
def connectors(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """List data connectors across all collections in the location.

    Connector-backed sources (Gmail, Drive, Jira, …) each live in their own
    collection, so this scans every collection's dataConnector singleton.
    Read-only (v1alpha REST GET — no published Python client exists for this
    surface). Requires only roles/discoveryengine.viewer.
    """
    state = ctx.obj
    clients = get_clients(state.project, state.location, getattr(state, "quota_project", None))
    try:
        connectors_data = collect_connectors(clients)
    except requests.HTTPError as exc:
        status = _http_status(exc)
        if status == 403:
            _print_permission_error(clients)
            raise typer.Exit(code=1) from None
        raise
    if not connectors_data:
        rendered = table("Data Connectors", ["Status"], [("no data connectors configured",)])
        output([], rendered, as_json)
        return
    rows = [
        (
            c["collection"],
            c["data_source"],
            c["state"],
            c["refresh_interval"],
            c["last_sync_time"],
            len(c["entities"]),
        )
        for c in connectors_data
    ]
    rendered = table(
        "Data Connectors",
        ["Collection", "Data Source", "State", "Refresh Interval", "Last Sync", "Entities"],
        rows,
    )
    output(connectors_data, rendered, as_json)


def _print_permission_error(clients: Clients) -> None:
    from geadm.render import err_console

    err_console.print(
        "[bold red]Permission denied[/bold red] reading the data connector "
        f"for {clients.collection_path}. Requires roles/discoveryengine.viewer."
    )


@app.command()
def agents(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """List assistant agents for every engine under the default collection.

    Read-only (v1alpha REST GET per engine — no published Python client
    exists for this surface). A per-engine 404/403 is reported as a note
    rather than aborting the command. Requires only
    roles/discoveryengine.viewer.
    """
    state = ctx.obj
    clients = get_clients(state.project, state.location, getattr(state, "quota_project", None))
    data = collect_agents(clients)
    rows = [
        (
            a["engine_id"],
            a.get("id", ""),
            a.get("display_name", ""),
            a.get("state", ""),
            a.get("note", a.get("error", "")),
        )
        for a in data
    ]
    rendered = table(
        "Agents",
        ["Engine", "Agent ID", "Display Name", "State", "Note"],
        rows,
    )
    output(data, rendered, as_json)


@app.command()
def licenses(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """List Gemini Enterprise user licenses in the default user store.

    Read-only (UserLicenseServiceClient.list_user_licenses). Requires only
    roles/discoveryengine.viewer.
    """
    from geadm.render import err_console

    state = ctx.obj
    clients = get_clients(state.project, state.location, getattr(state, "quota_project", None))
    try:
        data = collect_licenses(clients)
    except gexceptions.NotFound:
        rendered = table("User Licenses", ["Status"], [("no user store found",)])
        output([], rendered, as_json)
        return
    except gexceptions.PermissionDenied as exc:
        err_console.print(
            "[bold red]Permission denied[/bold red] listing user licenses for "
            f"{clients.project} ({clients.location}). Requires "
            "roles/discoveryengine.viewer.\n"
            f"[dim]{exc}[/dim]"
        )
        raise typer.Exit(code=1) from None
    rows = [
        (
            lic["user_principal"],
            lic["license_assignment_state"],
            lic["license_config_id"],
            lic["last_login_time"],
            lic["create_time"],
        )
        for lic in data
    ]
    rendered = table(
        "User Licenses",
        ["User", "State", "License Config", "Last Login", "Created"],
        rows,
    )
    output(data, rendered, as_json)
