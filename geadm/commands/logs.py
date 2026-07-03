"""`geadm logs` — inspect Gemini Enterprise Cloud Logging output.

Strictly read-only: the only RPC used is Cloud Logging's entries.list
(via google.cloud.logging_v2.Client.list_entries). This module never
writes or deletes log entries, sinks, or metrics.

Two subcommands:

  logs connector   Discovery Engine data-connector activity
                    (logName=".../connector_activity").
  logs user        Per-end-user API activity, scoped by principal email
                    (resource.type="consumed_api").

Reading logs only requires roles/logging.viewer. Actually *emitting*
connector/observability logs in the first place requires the caller (or
the service) to have roles/discoveryengine.agentspaceAdmin and to have
enabled Cloud Logging for the Discovery Engine / Agentspace app.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

import typer

from geadm import render
from geadm.duration import since_rfc3339

app = typer.Typer(
    help=(
        "Inspect Gemini Enterprise Cloud Logging output (read-only, "
        "roles/logging.viewer). Enabling connector/observability logging on a "
        "project requires roles/discoveryengine.agentspaceAdmin (one-time setup)."
    ),
    no_args_is_help=True,
)

# ---- shared constants -------------------------------------------------------

VALID_SEVERITIES = (
    "DEFAULT",
    "DEBUG",
    "INFO",
    "NOTICE",
    "WARNING",
    "ERROR",
    "CRITICAL",
    "ALERT",
    "EMERGENCY",
)

_CONNECTOR_LOG_ID = "discoveryengine.googleapis.com%2Fconnector_activity"


# ---- filter builders (pure, unit-testable) ----------------------------------


def connector_filter(
    project: str,
    datastore: Optional[str],
    severity: Optional[str],
    since: str,
) -> str:
    """Build the Cloud Logging filter for `logs connector`.

    The %2F-encoded slash in the logName is required by the Logging API;
    a literal "/" in connector_activity does not match.
    """
    clauses = [f'logName="projects/{project}/logs/{_CONNECTOR_LOG_ID}"']

    if datastore:
        # Substring (":") match against the dataConnector resource name
        # carried in jsonPayload.LogMetadata.name, so a bare datastore ID
        # is enough to narrow the results.
        clauses.append(f'jsonPayload.LogMetadata.name:"{datastore}"')

    if severity:
        sev = severity.strip().upper()
        if sev not in VALID_SEVERITIES:
            raise ValueError(
                f"Invalid --severity {severity!r}: expected one of "
                f"{', '.join(VALID_SEVERITIES)} (case-insensitive)."
            )
        clauses.append(f"severity>={sev}")

    clauses.append(f'timestamp>="{since_rfc3339(since)}"')
    return "\n".join(clauses)


def user_filter(project: str, email: str, since: str) -> str:
    """Build the Cloud Logging filter for `logs user <email>`.

    Scopes to consumed_api entries for the Discovery Engine service and
    narrows to a single principal via the audit-log identity field
    protoPayload.authenticationInfo.principalEmail.
    """
    del project  # entries.list already scopes to clients.project via resource_names
    clauses = [
        'resource.type="consumed_api"',
        'resource.labels.service="discoveryengine.googleapis.com"',
        f'protoPayload.authenticationInfo.principalEmail="{email}"',
        f'timestamp>="{since_rfc3339(since)}"',
    ]
    return "\n".join(clauses)


# ---- entry normalization -----------------------------------------------------


def _payload_to_dict(payload: Any) -> dict:
    """Best-effort, defensive conversion of a LogEntry payload to a dict."""
    if payload is None:
        return {}
    if isinstance(payload, Mapping):
        return dict(payload)
    if isinstance(payload, str):
        return {}
    # Assume a protobuf Message (e.g. protoPayload not already parsed to dict).
    try:
        from google.protobuf.json_format import MessageToDict

        return MessageToDict(payload, preserving_proto_field_name=True)
    except Exception:
        return {}


def _extract_message(payload: Any, payload_dict: dict) -> str:
    if isinstance(payload, str):
        return payload
    if payload_dict.get("message"):
        return str(payload_dict["message"])
    status = payload_dict.get("status")
    if isinstance(status, Mapping) and status.get("message"):
        return str(status["message"])
    method_name = payload_dict.get("methodName")
    if method_name:
        return str(method_name)
    if payload_dict:
        return str(payload_dict)
    return str(payload) if payload is not None else ""


def _extract_status(payload_dict: dict) -> Any:
    status = payload_dict.get("status")
    if status is None:
        return None
    if isinstance(status, Mapping):
        return dict(status)
    return str(status)


def _extract_entity_name(payload_dict: dict) -> Optional[str]:
    """Connector/entity resource name, when present (LogMetadata.name)."""
    log_metadata = payload_dict.get("LogMetadata")
    if isinstance(log_metadata, Mapping):
        name = log_metadata.get("name")
        if name:
            return str(name)
    return None


def _normalize_entry(entry: Any) -> dict:
    """Convert a google.cloud.logging_v2 LogEntry into a JSON-safe dict."""
    payload = getattr(entry, "payload", None)
    payload_dict = _payload_to_dict(payload)

    resource = getattr(entry, "resource", None)
    resource_type = getattr(resource, "type", None)
    resource_labels = getattr(resource, "labels", None) or {}

    timestamp = getattr(entry, "timestamp", None)

    return {
        "timestamp": timestamp.isoformat() if timestamp is not None else None,
        "severity": getattr(entry, "severity", None),
        "log_name": getattr(entry, "log_name", None),
        "message": _extract_message(payload, payload_dict),
        "status": _extract_status(payload_dict),
        "entity_name": _extract_entity_name(payload_dict),
        "resource_type": resource_type,
        "resource_labels": dict(resource_labels),
    }


def collect_entries(clients: Any, filter_str: str, limit: int) -> list[dict]:
    """List (read-only) and normalize Cloud Logging entries for a filter."""
    from google.cloud import logging_v2

    entries = clients.logging.list_entries(
        filter_=filter_str,
        order_by=logging_v2.DESCENDING,
        max_results=limit,
    )
    return [_normalize_entry(entry) for entry in entries]


# ---- rendering ---------------------------------------------------------------


def _render_table(title: str, rows: list[dict], show_entity: bool) -> Any:
    columns = ["Time", "Severity", "Message"]
    if show_entity:
        columns.insert(2, "Connector / Entity")

    table_rows = []
    for row in rows:
        sev = row.get("severity") or "DEFAULT"
        styled_sev = f"[{render.severity_style(sev)}]{sev}[/{render.severity_style(sev)}]"
        cells = [row.get("timestamp"), styled_sev]
        if show_entity:
            cells.append(row.get("entity_name"))
        cells.append(row.get("message"))
        table_rows.append(cells)

    return render.table(title, columns, table_rows)


# ---- commands ------------------------------------------------------------


@app.command()
def connector(
    ctx: typer.Context,
    datastore: Optional[str] = typer.Option(
        None,
        "--datastore",
        help="Restrict to a connector/datastore ID (substring match against "
        "the dataConnector resource name).",
    ),
    severity: Optional[str] = typer.Option(
        None,
        "--severity",
        help="Minimum severity: DEFAULT/DEBUG/INFO/NOTICE/WARNING/ERROR/"
        "CRITICAL/ALERT/EMERGENCY (case-insensitive).",
    ),
    since: str = typer.Option(
        "1h",
        "--since",
        help="Look back window, e.g. 30m, 1h, 24h, 7d.",
    ),
    limit: int = typer.Option(50, "--limit", help="Maximum entries to return."),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Show Discovery Engine data-connector activity logs.

    Reading these logs only requires roles/logging.viewer. Emitting
    connector/observability logs in the first place requires
    roles/discoveryengine.agentspaceAdmin and connector logging enabled
    on the Agentspace app/data connector.
    """
    from geadm.auth import get_clients

    state = ctx.obj
    clients = get_clients(state.project, state.location)

    try:
        filter_str = connector_filter(clients.project, datastore, severity, since)
    except ValueError as exc:
        render.err_console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from None

    rows = collect_entries(clients, filter_str, limit)

    title = f"Connector activity ({since})"
    table_ = _render_table(title, rows, show_entity=True)
    render.output(rows, table_, as_json)


@app.command()
def user(
    ctx: typer.Context,
    email: str = typer.Argument(..., help="Principal email to scope logs to."),
    since: str = typer.Option(
        "24h",
        "--since",
        help="Look back window, e.g. 30m, 1h, 24h, 7d.",
    ),
    limit: int = typer.Option(50, "--limit", help="Maximum entries to return."),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Show a single end user's Gemini Enterprise API activity.

    WARNING: results can include end-user prompt/response content when
    prompt/response logging is enabled on the project. Reading these logs
    only requires roles/logging.viewer; results depend entirely on
    prompt/response (and other observability) logging having been enabled
    for the project/app — if it isn't, this may return little or nothing.
    """
    # warn_banner MUST be the first thing printed: this command can surface
    # end-user prompt/response content, and callers need to see the warning
    # before anything else regardless of --json (it goes to stderr).
    render.warn_banner(
        "Output may include end-user prompt/response content if "
        "prompt/response logging is enabled on this project."
    )

    from geadm.auth import get_clients

    state = ctx.obj
    clients = get_clients(state.project, state.location)

    try:
        filter_str = user_filter(clients.project, email, since)
    except ValueError as exc:
        render.err_console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from None

    rows = collect_entries(clients, filter_str, limit)

    title = f"User activity: {email} ({since})"
    table_ = _render_table(title, rows, show_entity=False)
    render.output(rows, table_, as_json)
