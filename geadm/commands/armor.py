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


# ---- policy (Model Armor template) ------------------------------------------


def _discover_templates(clients: Any, since: str) -> list[tuple[str, str]]:
    """Distinct (location, template_id) pairs actually screening traffic,
    read from the sanitize log's resource labels."""
    from google.cloud import logging_v2

    filter_str = "\n".join(
        [
            f'logName="projects/{clients.project}/logs/{_ARMOR_LOG_ID}"',
            f'timestamp>="{since_rfc3339(since)}"',
        ]
    )
    seen: list[tuple[str, str]] = []
    for entry in clients.logging.list_entries(
        filter_=filter_str, order_by=logging_v2.DESCENDING, max_results=200
    ):
        labels = dict(getattr(getattr(entry, "resource", None), "labels", None) or {})
        pair = (labels.get("location"), labels.get("template_id"))
        if all(pair) and pair not in seen:
            seen.append(pair)  # type: ignore[arg-type]
    return seen  # type: ignore[return-value]


def template_rows(filter_config: dict) -> list[tuple[str, str, str]]:
    """(filter, status, detail) rows for a Model Armor filterConfig."""
    rows: list[tuple[str, str, str]] = []
    pi = filter_config.get("piAndJailbreakFilterSettings") or {}
    if pi:
        rows.append(
            (
                "Prompt injection & jailbreak",
                pi.get("filterEnforcement", "?"),
                pi.get("confidenceLevel", ""),
            )
        )
    for rai in (filter_config.get("raiSettings") or {}).get("raiFilters") or []:
        rows.append(
            (
                f"Responsible AI: {rai.get('filterType', '?')}",
                "ENABLED",
                rai.get("confidenceLevel", ""),
            )
        )
    mal = filter_config.get("maliciousUriFilterSettings") or {}
    if mal:
        rows.append(("Malicious URIs", mal.get("filterEnforcement", "?"), ""))
    csam = filter_config.get("csamFilterSettings") or {}
    if csam:
        rows.append(("CSAM", csam.get("filterEnforcement", "?"), ""))
    sdp = filter_config.get("sdpSettings") or {}
    if sdp:
        rows.append(("Sensitive data protection", "CONFIGURED", ""))
    return rows


def collect_policy(clients: Any, since: str) -> list[dict]:
    """Fetch the filter config of every Model Armor template in use."""
    templates: list[dict] = []
    for location, template_id in _discover_templates(clients, since):
        name = f"projects/{clients.project}/locations/{location}/templates/{template_id}"
        data = clients.rest_get(
            f"v1/{name}", host=f"modelarmor.{location}.rep.googleapis.com"
        )
        templates.append(
            {
                "name": data.get("name"),
                "template_id": template_id,
                "location": location,
                "labels": data.get("labels") or {},
                "filter_config": data.get("filterConfig") or {},
                "update_time": data.get("updateTime"),
            }
        )
    return templates


def summarise(rows: list[dict]) -> list[dict]:
    """Aggregate violation rows into per-filter hit counts with an example.

    A single entry can match several filters, so it counts toward each. The
    confidence suffix is stripped for grouping (pi_and_jailbreak, not
    pi_and_jailbreak(HIGH)). Rows arrive newest-first, so the first example
    kept per filter is the most recent one.
    """
    agg: dict[str, dict] = {}
    for row in rows:
        ts = row.get("timestamp")
        content = row.get("content")
        for f in row.get("matched_filters") or []:
            key = f.split("(", 1)[0]
            a = agg.setdefault(
                key, {"filter": key, "hits": 0, "last_seen": None, "example": None}
            )
            a["hits"] += 1
            if ts and (a["last_seen"] is None or ts > a["last_seen"]):
                a["last_seen"] = ts
            if a["example"] is None and content:
                a["example"] = content
    return sorted(agg.values(), key=lambda a: (-a["hits"], a["filter"]))


def _render_summary(summary: list[dict], since: str) -> Any:
    from rich.text import Text

    if not summary:
        return Text(f"No Model Armor violations in the last {since}.", style="green")
    rows = [
        (
            s["filter"],
            str(s["hits"]),
            s["last_seen"] or "",
            _snippet(s["example"], 60),
        )
        for s in summary
    ]
    return render.table(
        f"Model Armor hits by filter ({since})",
        ["Filter", "Hits", "Last seen", "Example input"],
        rows,
    )


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


def _render_policy(templates: list[dict]) -> Any:
    from rich.console import Group
    from rich.text import Text

    if not templates:
        return Text(
            "No Model Armor templates found screening this project in the window.",
            style="yellow",
        )
    pieces: list[Any] = []
    for tmpl in templates:
        rows = [
            (name, status, detail)
            for name, status, detail in template_rows(tmpl["filter_config"])
        ]
        pieces.append(
            render.table(
                f"Model Armor policy — {tmpl['template_id']} ({tmpl['location']})",
                ["Filter", "Enforcement", "Confidence"],
                rows or [("[dim]no filters configured[/dim]", "", "")],
            )
        )
    return Group(*pieces)


def armor_command(
    ctx: typer.Context,
    policy: bool = typer.Option(
        False, "--policy", help="Print the configured Model Armor template(s) instead."
    ),
    summary: bool = typer.Option(
        False, "--summary", help="Aggregate violations by filter category."
    ),
    since: str = typer.Option("24h", "--since", help="Look-back window, e.g. 1h, 24h, 7d."),
    all_: bool = typer.Option(
        False, "--all", help="Include screenings that passed (not just violations)."
    ),
    limit: int = typer.Option(50, "--limit", help="Maximum entries to return."),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Show Model Armor violations, a per-filter summary, or the policy."""
    from geadm.auth import get_clients

    state = ctx.obj
    clients = get_clients(state.project, state.location, getattr(state, "quota_project", None))

    if policy:
        # Policy is configuration, not screened content — no banner.
        try:
            templates = collect_policy(clients, since)
        except ValueError as exc:
            render.err_console.print(f"[bold red]Error:[/bold red] {exc}")
            raise typer.Exit(code=1) from None
        render.output(templates, _render_policy(templates), as_json)
        return

    # Surfaces the screened prompt/response text, so warn first.
    render.warn_banner(
        "Output includes prompt/response content that Model Armor screened."
    )
    matched_only = summary or not all_

    try:
        filter_str = armor_filter(clients.project, since, matched_only)
    except ValueError as exc:
        render.err_console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from None

    rows = collect_violations(clients, filter_str, limit)

    if summary:
        aggregates = summarise(rows)
        render.output(aggregates, _render_summary(aggregates, since), as_json)
        return

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
