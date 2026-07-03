"""`geadm logs` — inspect Gemini Enterprise Cloud Logging output.

Strictly read-only: the only RPC used is Cloud Logging's entries.list
(via google.cloud.logging_v2.Client.list_entries). This module never
writes or deletes log entries, sinks, or metrics.

Two subcommands:

  logs connector   Discovery Engine data-connector activity
                    (logName=".../connector_activity").
  logs user        Per-end-user Gemini Enterprise activity, scoped by
                    jsonPayload.userIamPrincipal on the
                    gemini_enterprise_user_activity log.

Reading logs only requires roles/logging.viewer. Actually *emitting*
connector/observability logs in the first place requires the caller (or
the service) to have roles/discoveryengine.agentspaceAdmin and to have
enabled Cloud Logging for the Discovery Engine / Agentspace app.
"""

from __future__ import annotations

import re
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
    clauses = connector_base_clauses(project, datastore, severity)
    clauses.append(f'timestamp>="{since_rfc3339(since)}"')
    return "\n".join(clauses)


def connector_base_clauses(
    project: str, datastore: Optional[str], severity: Optional[str]
) -> list[str]:
    """connector_filter without the timestamp clause (follow mode appends
    its own cursor)."""
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

    return clauses


_USER_ACTIVITY_LOG_ID = "discoveryengine.googleapis.com%2Fgemini_enterprise_user_activity"


def user_filter(project: str, email: Optional[str], since: str) -> str:
    """Build the Cloud Logging filter for `logs user [email]`.

    Gemini Enterprise writes per-user query/assist activity to the
    gemini_enterprise_user_activity log; the caller identity lives in
    jsonPayload.userIamPrincipal (verified against live GE entries — these
    are platform logs, not audit logs, so there is no protoPayload).

    email None (or "*" / "all") means all users: the principal clause is
    omitted entirely.
    """
    clauses = user_base_clauses(project, email)
    clauses.append(f'timestamp>="{since_rfc3339(since)}"')
    return "\n".join(clauses)


def user_base_clauses(project: str, email: Optional[str]) -> list[str]:
    """user_filter without the timestamp clause (follow mode appends its
    own cursor)."""
    clauses = [f'logName="projects/{project}/logs/{_USER_ACTIVITY_LOG_ID}"']
    if email and email not in ("*", "all"):
        clauses.append(f'jsonPayload.userIamPrincipal="{email}"')
    return clauses


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


def _extract_prompt_text(payload_dict: dict) -> Optional[str]:
    """Prompt text from a gemini_enterprise_user_activity entry
    (jsonPayload.request.query.parts[].text), when present."""
    request = payload_dict.get("request")
    if not isinstance(request, Mapping):
        return None
    query = request.get("query")
    if not isinstance(query, Mapping):
        return None
    parts = query.get("parts")
    if not isinstance(parts, list):
        return None
    texts = [p.get("text") for p in parts if isinstance(p, Mapping) and p.get("text")]
    return " ".join(texts) if texts else None


def _log_metadata(payload_dict: dict) -> Mapping:
    """jsonPayload metadata block; connector logs spell it LogMetadata,
    user-activity logs spell it logMetadata."""
    for key in ("LogMetadata", "logMetadata"):
        value = payload_dict.get(key)
        if isinstance(value, Mapping):
            return value
    return {}


def _extract_message(payload: Any, payload_dict: dict) -> str:
    if isinstance(payload, str):
        return payload
    if payload_dict.get("message"):
        return str(payload_dict["message"])
    status = payload_dict.get("status")
    if isinstance(status, Mapping) and status.get("message"):
        return str(status["message"])
    method_name = payload_dict.get("methodName") or _log_metadata(payload_dict).get(
        "methodName"
    )
    prompt = _extract_prompt_text(payload_dict)
    if method_name and prompt:
        return f"{method_name}: {prompt}"
    if prompt:
        return prompt
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
    """Connector/entity resource name, when present ((L|l)ogMetadata.name)."""
    name = _log_metadata(payload_dict).get("name")
    return str(name) if name else None


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
        "user": payload_dict.get("userIamPrincipal"),
        "resource_type": resource_type,
        "resource_labels": dict(resource_labels),
        "insert_id": getattr(entry, "insert_id", None),
    }


def _log_exists(clients: Any, log_id: str) -> Optional[bool]:
    """Whether the project has ever retained entries for this log ID.

    Uses the read-only Logging logs.list method (roles/logging.viewer).
    Returns None when the check itself fails, so callers stay silent
    rather than guessing.
    """
    from urllib.parse import unquote

    try:
        names: list[str] = []
        page_token: Optional[str] = None
        while True:
            params = {"pageSize": "200"}
            if page_token:
                params["pageToken"] = page_token
            data = clients.rest_get(
                f"v2/projects/{clients.project}/logs",
                params=params,
                host="logging.googleapis.com",
            )
            names.extend(data.get("logNames") or [])
            page_token = data.get("nextPageToken")
            if not page_token:
                break
    except Exception:
        return None
    target = unquote(log_id)
    return any(unquote(n.rsplit("/logs/", 1)[-1]) == target for n in names)


def _print_empty_hint(clients: Any, log_id: str, label: str, enable_hint: str) -> None:
    """Explain an empty result: distinguish 'log never written' (logging not
    enabled) from 'log exists but nothing matched the filter/window'."""
    from urllib.parse import unquote

    exists = _log_exists(clients, log_id)
    if exists is False:
        render.err_console.print(
            f"[yellow]Note:[/yellow] the {label} log "
            f"([bold]{unquote(log_id)}[/bold]) has never been written in "
            f"project {clients.project} (or has no retained entries). "
            f"{enable_hint}"
        )
    elif exists:
        render.err_console.print(
            f"[dim]The {label} log exists; no entries matched the filter/time window.[/dim]"
        )


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


def _render_table(
    title: str, rows: list[dict], show_entity: bool, show_user: bool = False
) -> Any:
    columns = ["Time", "Severity", "Message"]
    if show_user:
        columns.insert(2, "User")
    if show_entity:
        columns.insert(2, "Connector / Entity")

    table_rows = []
    for row in rows:
        sev = row.get("severity") or "DEFAULT"
        styled_sev = f"[{render.severity_style(sev)}]{sev}[/{render.severity_style(sev)}]"
        cells = [row.get("timestamp"), styled_sev]
        if show_entity:
            cells.append(row.get("entity_name"))
        if show_user:
            cells.append(row.get("user"))
        cells.append(row.get("message"))
        table_rows.append(cells)

    return render.table(title, columns, table_rows)


_RESOURCE_PREFIX = re.compile(
    r"^projects/[^/]+/locations/[^/]+/?(collections/default_collection/?)?(engines/)?"
)


def _short_resource(name: Optional[str]) -> Optional[str]:
    """Compact a resource name for tail output: drop the project/location
    (and default_collection/engines) boilerplate, keep the meaningful tail."""
    if not name:
        return None
    short = _RESOURCE_PREFIX.sub("", name)
    return short or None


def _short_time(timestamp: Optional[str]) -> str:
    """Local HH:MM:SS for tail output."""
    if not timestamp:
        return "--:--:--"
    try:
        from datetime import datetime

        return datetime.fromisoformat(timestamp).astimezone().strftime("%H:%M:%S")
    except ValueError:
        return timestamp


def _print_follow_row(row: dict, show_user: bool, as_json: bool) -> None:
    """One streamed entry in follow mode: NDJSON with --json, else a line."""
    if as_json:
        import json

        print(json.dumps(row, default=str), flush=True)
        return
    sev = row.get("severity") or "DEFAULT"
    parts = [
        f"[dim]{_short_time(row.get('timestamp'))}[/dim]",
        f"[{render.severity_style(sev)}]{sev:<8}[/{render.severity_style(sev)}]",
    ]
    if show_user and row.get("user"):
        parts.append(f"[cyan]{row['user']}[/cyan]")
    entity = _short_resource(row.get("entity_name"))
    if entity:
        parts.append(f"[magenta]{entity}[/magenta]")
    parts.append(row.get("message") or "")
    render.console.print(" ".join(parts), highlight=False)


def _follow(
    clients: Any,
    base_filter: str,
    show_user: bool,
    as_json: bool,
    interval: float = 5.0,
) -> None:
    """Poll entries.list forever, printing new entries as they arrive.

    `base_filter` must NOT contain a timestamp clause; the cursor is
    appended per poll. Read-only, Ctrl+C to stop.
    """
    import time as _time
    from datetime import datetime, timezone

    from google.cloud import logging_v2

    if not as_json:
        render.err_console.print(
            f"[dim]Following (poll every {interval:.0f}s) — Ctrl+C to stop.[/dim]"
        )
    cursor = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    seen: set[str] = set()
    while True:
        filter_str = f'{base_filter}\ntimestamp>="{cursor}"'
        entries = clients.logging.list_entries(
            filter_=filter_str, order_by=logging_v2.ASCENDING, page_size=100
        )
        for entry in entries:
            row = _normalize_entry(entry)
            key = row.get("insert_id") or f"{row.get('timestamp')}|{row.get('message')}"
            if key in seen:
                continue
            seen.add(key)
            _print_follow_row(row, show_user, as_json)
            if row.get("timestamp"):
                cursor = row["timestamp"]
        # keep the dedupe set bounded; cursor overlap only needs recent ids
        if len(seen) > 1000:
            seen = set(list(seen)[-200:])
        _time.sleep(interval)


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
    follow: bool = typer.Option(
        False,
        "--follow",
        "-f",
        help="Stream new entries as they arrive (polls every 5s; Ctrl+C to "
        "stop). With --json, emits newline-delimited JSON.",
    ),
) -> None:
    """Show Discovery Engine data-connector activity logs.

    Reading these logs only requires roles/logging.viewer. Emitting
    connector/observability logs in the first place requires
    roles/discoveryengine.agentspaceAdmin and connector logging enabled
    on the Agentspace app/data connector.
    """
    from geadm.auth import get_clients

    state = ctx.obj
    clients = get_clients(state.project, state.location, getattr(state, "quota_project", None))

    try:
        filter_str = connector_filter(clients.project, datastore, severity, since)
        base_clauses = connector_base_clauses(clients.project, datastore, severity)
    except ValueError as exc:
        render.err_console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from None

    if follow:
        _follow(clients, "\n".join(base_clauses), show_user=False, as_json=as_json)
        return

    rows = collect_entries(clients, filter_str, limit)

    title = f"Connector activity ({since})"
    table_ = _render_table(title, rows, show_entity=True)
    render.output(rows, table_, as_json)
    if not rows:
        _print_empty_hint(
            clients,
            _CONNECTOR_LOG_ID,
            "connector activity",
            "Connector-activity logging is likely not enabled for this project; "
            "enabling it requires roles/discoveryengine.agentspaceAdmin.",
        )


@app.command()
def user(
    ctx: typer.Context,
    email: Optional[str] = typer.Argument(
        None,
        help="Principal email to scope logs to. Omit (or pass 'all') for "
        "every user's activity. A bare * is expanded by your shell — "
        "quote it or just leave the argument off.",
    ),
    since: str = typer.Option(
        "24h",
        "--since",
        help="Look back window, e.g. 30m, 1h, 24h, 7d.",
    ),
    limit: int = typer.Option(50, "--limit", help="Maximum entries to return."),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
    follow: bool = typer.Option(
        False,
        "--follow",
        "-f",
        help="Stream new entries as they arrive (polls every 5s; Ctrl+C to "
        "stop). With --json, emits newline-delimited JSON.",
    ),
) -> None:
    """Show end users' Gemini Enterprise activity (one user, or all).

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
    clients = get_clients(state.project, state.location, getattr(state, "quota_project", None))
    all_users = not email or email in ("*", "all")

    try:
        filter_str = user_filter(clients.project, email, since)
        base_clauses = user_base_clauses(clients.project, email)
    except ValueError as exc:
        render.err_console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from None

    if follow:
        _follow(clients, "\n".join(base_clauses), show_user=all_users, as_json=as_json)
        return

    rows = collect_entries(clients, filter_str, limit)

    who = "all users" if all_users else email
    title = f"User activity: {who} ({since})"
    table_ = _render_table(title, rows, show_entity=False, show_user=all_users)
    render.output(rows, table_, as_json)
    if not rows:
        _print_empty_hint(
            clients,
            _USER_ACTIVITY_LOG_ID,
            "Gemini Enterprise user activity",
            "Observability / prompt-response logging is likely not enabled for "
            "this project (requires roles/discoveryengine.agentspaceAdmin), or "
            "this principal has no activity.",
        )
