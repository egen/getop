"""geadm armor — surface Model Armor screening violations.

Reads the Model Armor sanitize-operations log
(modelarmor.googleapis.com/sanitize_operations), which records every
prompt/response screened by the Model Armor template wired into the Gemini
Enterprise app, and the per-filter verdict. By default only violations
(filterMatchState = MATCH_FOUND) are shown. Read-only: entries.list only.
"""

from __future__ import annotations

from typing import Any, Optional

import typer

from geadm import render
from geadm.commands.logs import _print_empty_hint, _snippet, collect_entries
from geadm.duration import since_rfc3339

_ARMOR_LOG_ID = "modelarmor.googleapis.com%2Fsanitize_operations"

# filter-group key -> the *FilterResult wrapper key Model Armor nests under it.
_FILTER_KEYS = {
    "pi_and_jailbreak": "piAndJailbreakFilterResult",
    "rai": "raiFilterResult",
    "csam": "csamFilterFilterResult",
    "malicious_uris": "maliciousUriFilterResult",
}


def armor_filter(project: str, since: str, matched_only: bool) -> str:
    """Cloud Logging filter for the Model Armor sanitize-operations log.

    matched_only restricts to violations; severity is INFO even on a match,
    so the match state is the only reliable discriminator.
    """
    clauses = armor_base_clauses(project, matched_only)
    clauses.append(f'timestamp>="{since_rfc3339(since)}"')
    return "\n".join(clauses)


def armor_base_clauses(project: str, matched_only: bool) -> list[str]:
    clauses = [f'logName="projects/{project}/logs/{_ARMOR_LOG_ID}"']
    if matched_only:
        clauses.append('jsonPayload.sanitizationResult.filterMatchState="MATCH_FOUND"')
    return clauses


def _matched_filters(filter_results: Any) -> list[str]:
    """Names (with confidence) of the filters that tripped, e.g.
    ['pi_and_jailbreak(HIGH)', 'rai:dangerous(HIGH)']. RAI expands to its
    matched sub-types; other filters report at the group level."""
    if not isinstance(filter_results, dict):
        return []
    matched: list[str] = []
    for group, inner_key in _FILTER_KEYS.items():
        inner = filter_results.get(group)
        if not isinstance(inner, dict):
            continue
        result = inner.get(inner_key)
        if not isinstance(result, dict):
            continue
        if group == "rai":
            for sub, sub_res in (result.get("raiFilterTypeResults") or {}).items():
                if isinstance(sub_res, dict) and sub_res.get("matchState") == "MATCH_FOUND":
                    conf = sub_res.get("confidenceLevel")
                    matched.append(f"rai:{sub}({conf})" if conf else f"rai:{sub}")
            continue
        if result.get("matchState") == "MATCH_FOUND":
            conf = result.get("confidenceLevel")
            matched.append(f"{group}({conf})" if conf else group)
    return matched


def _normalize(entry: Any) -> dict:
    """Model Armor sanitize entry → JSON-safe row."""
    from collections.abc import Mapping

    payload = getattr(entry, "payload", None)
    payload_dict = dict(payload) if isinstance(payload, Mapping) else {}
    result = payload_dict.get("sanitizationResult")
    result = result if isinstance(result, dict) else {}
    op = payload_dict.get("operationType") or ""
    direction = {
        "SANITIZE_USER_PROMPT": "prompt",
        "SANITIZE_MODEL_RESPONSE": "response",
    }.get(op, op)
    resource = getattr(entry, "resource", None)
    labels = dict(getattr(resource, "labels", None) or {})
    ts = getattr(entry, "timestamp", None)
    input_ = payload_dict.get("sanitizationInput")
    return {
        "timestamp": ts.isoformat() if ts is not None else None,
        "direction": direction,
        "match_state": result.get("filterMatchState"),
        "matched_filters": _matched_filters(result.get("filterResults")),
        "content": input_.get("text") if isinstance(input_, dict) else None,
        "template": labels.get("template_id"),
        "location": labels.get("location"),
        "insert_id": getattr(entry, "insert_id", None),
    }


def collect_violations(clients: Any, filter_str: str, limit: int) -> list[dict]:
    from google.cloud import logging_v2

    entries = clients.logging.list_entries(
        filter_=filter_str, order_by=logging_v2.DESCENDING, max_results=limit
    )
    return [_normalize(e) for e in entries]


def _render_table(rows: list[dict], since: str, matched_only: bool) -> Any:
    scope = "violations" if matched_only else "screenings"
    title = f"Model Armor {scope} ({since})"
    table_rows = []
    for row in rows:
        state = row.get("match_state") or ""
        filters = ", ".join(row.get("matched_filters") or []) or (
            "[dim]—[/dim]" if state != "MATCH_FOUND" else ""
        )
        state_styled = (
            f"[bold red]{state}[/bold red]" if state == "MATCH_FOUND" else f"[dim]{state}[/dim]"
        )
        table_rows.append(
            [
                row.get("timestamp"),
                row.get("direction"),
                state_styled,
                filters,
                _snippet(row.get("content"), 60),
            ]
        )
    return render.table(
        title,
        ["Time", "Direction", "Match", "Filters", "Content"],
        table_rows,
    )


def armor_command(
    ctx: typer.Context,
    since: str = typer.Option("24h", "--since", help="Look-back window, e.g. 1h, 24h, 7d."),
    all_: bool = typer.Option(
        False, "--all", help="Include screenings that passed (not just violations)."
    ),
    limit: int = typer.Option(50, "--limit", help="Maximum entries to return."),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Show Model Armor violations (screened prompts/responses that tripped a filter)."""
    # Surfaces the screened prompt/response text, so warn first.
    render.warn_banner(
        "Output includes prompt/response content that Model Armor screened."
    )

    from geadm.auth import get_clients

    state = ctx.obj
    clients = get_clients(state.project, state.location, getattr(state, "quota_project", None))
    matched_only = not all_

    try:
        filter_str = armor_filter(clients.project, since, matched_only)
    except ValueError as exc:
        render.err_console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from None

    rows = collect_violations(clients, filter_str, limit)
    render.output(rows, _render_table(rows, since, matched_only), as_json)
    if not rows:
        _print_empty_hint(
            clients,
            _ARMOR_LOG_ID,
            "Model Armor sanitize-operations",
            "Model Armor may not be configured for this project's Gemini "
            "Enterprise app, or nothing was screened in the window. This log "
            "carries no user identity — use `geadm logs user` for that.",
        )
