"""`getop stats` — query volume, latency and connector sync freshness.

Reads Cloud Monitoring time series for discoveryengine.googleapis.com
metrics. Strictly read-only (list_metric_descriptors / list_time_series
only) — needs only roles/monitoring.viewer on the target project.

The set of published discoveryengine.googleapis.com metrics is not
reliably documented, so this command always discovers what exists at
runtime via list_metric_descriptors before querying any values; it never
assumes a specific metric type is present.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import typer
from google.api import metric_pb2
from google.api_core import exceptions as gexceptions
from google.cloud import monitoring_v3
from rich.console import Group
from rich.text import Text

from getop.duration import since_timestamp
from getop.render import err_console, output, table

# google.cloud.monitoring_v3 re-exports request/response/aggregation types but
# not MetricDescriptor itself (it stays in the underlying google.api proto).
MetricDescriptor = metric_pb2.MetricDescriptor

METRIC_FILTER = 'metric.type = starts_with("discoveryengine.googleapis.com/")'

_CONNECTOR_HINTS = ("connector", "sync")
_LATENCY_HINTS = ("latenc", "total_latency")


def _category_for(metric_type: str, value_type: Any) -> str:
    """Bucket a discovered metric type into volume / latency / connector."""
    lowered = metric_type.lower()
    if any(hint in lowered for hint in _CONNECTOR_HINTS):
        return "connector"
    is_distribution = (
        value_type == MetricDescriptor.ValueType.DISTRIBUTION
    )
    if is_distribution or any(hint in lowered for hint in _LATENCY_HINTS):
        return "latency"
    return "volume"


def _engine_label_key(descriptor: Any) -> Optional[str]:
    """Return the descriptor's metric label key that identifies an engine, if any."""
    for label in descriptor.labels:
        key_lower = label.key.lower()
        if "engine" in key_lower:
            return label.key
    return None


def _aggregation_for(
    metric_kind: Any, value_type: Any, category: str, alignment_seconds: int
) -> tuple[Any, str]:
    """Pick a simple, robust Aggregation for a metric, plus a label for the aggregate."""
    period = {"seconds": alignment_seconds}
    is_distribution = value_type == MetricDescriptor.ValueType.DISTRIBUTION
    is_cumulative_or_delta = metric_kind in (
        MetricDescriptor.MetricKind.CUMULATIVE,
        MetricDescriptor.MetricKind.DELTA,
    )

    if is_distribution:
        aligner = monitoring_v3.Aggregation.Aligner.ALIGN_PERCENTILE_95
        reducer = monitoring_v3.Aggregation.Reducer.REDUCE_PERCENTILE_95
        aggregate_label = "p95"
    elif is_cumulative_or_delta:
        aligner = monitoring_v3.Aggregation.Aligner.ALIGN_SUM
        reducer = monitoring_v3.Aggregation.Reducer.REDUCE_SUM
        aggregate_label = "sum"
    else:
        aligner = monitoring_v3.Aggregation.Aligner.ALIGN_MEAN
        reducer = monitoring_v3.Aggregation.Reducer.REDUCE_MEAN
        aggregate_label = "mean"

    aggregation = monitoring_v3.Aggregation(
        alignment_period=period,
        per_series_aligner=aligner,
        cross_series_reducer=reducer,
        group_by_fields=[],
    )
    return aggregation, aggregate_label


def _point_value(point: Any) -> Optional[float]:
    value = point.value
    for field in ("double_value", "int64_value"):
        if value._pb.HasField(field):
            return getattr(value, field)
    return None


def collect_stats(
    clients: Any,
    since: str,
    engine: str | None = None,
    categories: set[str] | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> dict:
    """Discover discoveryengine metrics and summarise volume/latency/connector sync.

    Pure, read-only: list_metric_descriptors + list_time_series only.
    Raises ValueError if `since` is malformed, and propagates any
    google.api_core.exceptions raised by the API (e.g. PermissionDenied)
    so the caller can render a clean error.

    `categories` restricts which metric categories ("query_volume", "latency",
    "connector") get per-metric time-series queries; discovery still lists
    everything. None means all categories (the default for `getop stats`).
    """
    start_time = since_timestamp(since)  # raises ValueError on bad --since
    end_time = datetime.now(timezone.utc)
    interval = monitoring_v3.TimeInterval(start_time=start_time, end_time=end_time)

    project_path = clients.monitoring_project_path
    client = clients.monitoring

    descriptors = list(
        client.list_metric_descriptors(
            request=monitoring_v3.ListMetricDescriptorsRequest(
                name=project_path, filter=METRIC_FILTER
            )
        )
    )

    result: dict[str, Any] = {
        "since": since,
        "engine": engine,
        "metrics_discovered": [d.type for d in descriptors],
        "query_volume": {},
        "latency": {},
        "connector": {"freshest_point": None, "metrics": {}},
    }
    if not descriptors:
        return result

    alignment_seconds = 300

    def summarize(descriptor: Any) -> tuple[str, dict]:
        """Query and summarise one metric (runs on a worker thread)."""
        category = _category_for(descriptor.type, descriptor.value_type)
        aggregation, aggregate_label = _aggregation_for(
            descriptor.metric_kind, descriptor.value_type, category, alignment_seconds
        )

        metric_filter = f'metric.type = "{descriptor.type}"'
        engine_applied = False
        if engine:
            label_key = _engine_label_key(descriptor)
            if label_key:
                metric_filter += f' AND metric.labels.{label_key} = "{engine}"'
                engine_applied = True

        try:
            series_list = list(
                client.list_time_series(
                    request=monitoring_v3.ListTimeSeriesRequest(
                        name=project_path,
                        filter=metric_filter,
                        interval=interval,
                        aggregation=aggregation,
                        view=monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
                    )
                )
            )
        except gexceptions.InvalidArgument:
            # Aggregation not valid for this metric's kind/value_type combo;
            # skip rather than fail the whole command.
            series_list = []

        points: list[Any] = []
        for series in series_list:
            points.extend(series.points)

        values = [v for v in (_point_value(p) for p in points) if v is not None]
        latest_iso: Optional[str] = None
        if points:
            latest_end = max(p.interval.end_time for p in points)
            latest_iso = latest_end.isoformat()

        if aggregate_label == "sum":
            aggregate = sum(values) if values else None
        elif values:
            aggregate = max(values) if aggregate_label == "p95" else sum(values) / len(values)
        else:
            aggregate = None

        return category, {
            "type": descriptor.type,
            "category": category,
            "metric_kind": MetricDescriptor.MetricKind.Name(descriptor.metric_kind),
            "value_type": MetricDescriptor.ValueType.Name(descriptor.value_type),
            "points": len(points),
            "aggregate": aggregate,
            "aggregate_label": aggregate_label,
            "latest_point_time": latest_iso,
            "engine_filter_applied": engine_applied,
        }

    wanted = [
        d
        for d in descriptors
        if categories is None or _category_for(d.type, d.value_type) in categories
    ]

    done = 0
    connector_latest: Optional[str] = None
    with ThreadPoolExecutor(max_workers=12) as pool:
        for category, summary in pool.map(summarize, wanted):
            done += 1
            if progress:
                progress(summary["type"], done, len(wanted))
            latest_iso = summary["latest_point_time"]
            if category == "connector":
                result["connector"]["metrics"][summary["type"]] = summary
                if latest_iso and (connector_latest is None or latest_iso > connector_latest):
                    connector_latest = latest_iso
            elif category == "latency":
                result["latency"][summary["type"]] = summary
            else:
                result["query_volume"][summary["type"]] = summary

    result["connector"]["freshest_point"] = connector_latest

    if engine and not any(
        m["engine_filter_applied"]
        for bucket in (result["query_volume"], result["latency"], result["connector"]["metrics"])
        for m in bucket.values()
    ):
        result["engine_filter_note"] = (
            f"--engine {engine!r} was ignored: no discovered metric exposes an "
            "engine-identifying label."
        )

    return result


_INT64_MAX = 9.2e18  # limits at int64-max mean "effectively unlimited"


def _fmt_number(value: Any) -> str:
    """Human-readable number: thousands separators, no scientific notation."""
    if value is None:
        return "-"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if v >= _INT64_MAX:
        return "unlimited"
    if v == int(v):
        return f"{int(v):,}"
    if abs(v) >= 100:
        return f"{v:,.0f}"
    if abs(v) >= 1:
        return f"{v:,.2f}"
    return f"{v:.4f}"


def _fmt_bytes(value: Any) -> str:
    """Binary-unit rendering for byte-denominated quotas."""
    if value is None:
        return "-"
    v = float(value)
    if v >= _INT64_MAX:
        return "unlimited"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(v) < 1024 or unit == "PiB":
            return f"{v:,.1f} {unit}" if unit != "B" else f"{int(v):,} B"
        v /= 1024
    return _fmt_number(value)


def _render(data: dict, header: str) -> Any:
    if not data["metrics_discovered"]:
        return Text(
            "No discoveryengine.googleapis.com metrics were found for this project/"
            "window. The product may be unused here, or metrics have not been "
            "emitted yet.",
            style="yellow",
        )

    rows = []
    for bucket in (data["query_volume"], data["latency"], data["connector"]["metrics"]):
        for m in bucket.values():
            latest = m["latest_point_time"] or "-"
            agg = (
                "-"
                if m["aggregate"] is None
                else f"{_fmt_number(m['aggregate'])} ({m['aggregate_label']})"
            )
            rows.append((m["type"], m["category"], m["points"], agg, latest))

    t = table(
        f"Discovery Engine metrics — {header}",
        ["Metric type", "Category", "Points", "Aggregate", "Latest point (UTC)"],
        rows,
    )

    freshest = data["connector"]["freshest_point"]
    if data["connector"]["metrics"]:
        conn_line = (
            f"Connector sync freshest data point: {freshest}"
            if freshest
            else "Connector sync metrics discovered but no data points in window."
        )
    else:
        conn_line = "No connector/sync metrics discovered."

    pieces = [t, Text(""), Text(conn_line)]
    note = data.get("engine_filter_note")
    if note:
        pieces.append(Text(note, style="yellow"))
    return Group(*pieces)


_QUOTA_FILTER = 'metric.type = starts_with("discoveryengine.googleapis.com/quota/")'


def collect_quota(clients: Any, since: str = "24h") -> list[dict]:
    """Discovery Engine quota rows: usage vs limit (+ % used) per quota and
    location, and exceeded-event counts over the --since window.

    Quota metrics follow quota/<name>/{usage,limit,exceeded} on the
    discoveryengine.googleapis.com/Location resource. Read-only.
    """
    start_time = since_timestamp(since)
    end_time = datetime.now(timezone.utc)
    interval = monitoring_v3.TimeInterval(start_time=start_time, end_time=end_time)
    client = clients.monitoring
    project_path = clients.monitoring_project_path

    descriptors = list(
        client.list_metric_descriptors(
            request=monitoring_v3.ListMetricDescriptorsRequest(
                name=project_path, filter=_QUOTA_FILTER
            )
        )
    )

    def fetch(descriptor: Any) -> tuple[str, list[Any]]:
        series = list(
            client.list_time_series(
                request=monitoring_v3.ListTimeSeriesRequest(
                    name=project_path,
                    filter=f'metric.type = "{descriptor.type}"',
                    interval=interval,
                    view=monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
                )
            )
        )
        return descriptor.type, series

    rows: dict[tuple[str, str], dict] = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        for metric_type, series_list in pool.map(fetch, descriptors):
            # quota/<name>/<kind>
            parts = metric_type.split("/quota/", 1)[-1].rsplit("/", 1)
            if len(parts) != 2:
                continue
            quota_name, kind = parts
            for series in series_list:
                if not series.points:
                    continue
                location = dict(series.resource.labels).get("location", "global")
                row = rows.setdefault(
                    (quota_name, location),
                    {
                        "quota": quota_name,
                        "location": location,
                        "limit_name": dict(series.metric.labels).get("limit_name"),
                        "usage": None,
                        "limit": None,
                        "exceeded": 0,
                        "percent_used": None,
                    },
                )
                latest = max(series.points, key=lambda p: p.interval.end_time)
                value = _point_value(latest)
                if kind == "usage":
                    row["usage"] = value
                elif kind == "limit":
                    row["limit"] = value
                elif kind == "exceeded":
                    values = [_point_value(p) or 0 for p in series.points]
                    row["exceeded"] += int(sum(values))

    for row in rows.values():
        usage, limit = row["usage"], row["limit"]
        if usage is not None and limit and 0 < limit < _INT64_MAX:
            row["percent_used"] = round(100.0 * usage / limit, 2)

    return sorted(
        rows.values(),
        key=lambda r: (-(r["percent_used"] or -1), r["quota"], r["location"]),
    )


def _pct_text(pct: Any) -> Any:
    if pct is None:
        return Text("-", style="dim")
    style = "bold red" if pct >= 90 else "bold yellow" if pct >= 75 else "green"
    return Text(f"{pct:.1f}%", style=style)


def _quota_console_url(project: str) -> str:
    """Cloud console quotas page for the project, pre-filtered to Discovery
    Engine. Terminals that support OSC 8 hyperlinks make the quota names
    clickable; others show plain text."""
    return (
        "https://console.cloud.google.com/iam-admin/quotas"
        f"?project={project}&service=discoveryengine.googleapis.com"
    )


def _render_quota(rows: list[dict], since: str, header: str, project: str) -> Any:
    if not rows:
        return Text(
            "No discoveryengine.googleapis.com quota metrics with data were "
            "found for this project/window.",
            style="yellow",
        )
    url = _quota_console_url(project)
    fmt_rows = []
    for r in rows:
        size_quota = "size" in r["quota"]
        fmt = _fmt_bytes if size_quota else _fmt_number
        fmt_rows.append(
            (
                Text(r["quota"], style=f"underline link {url}"),
                r["location"],
                fmt(r["usage"]),
                fmt(r["limit"]),
                _pct_text(r["percent_used"]),
                r["exceeded"] or "",
            )
        )
    return table(
        f"Discovery Engine quotas — {header}\n(exceeded counts over {since})",
        ["Quota", "Location", "Usage", "Limit", "Used", f"Exceeded ({since})"],
        fmt_rows,
    )


def quota_command(
    ctx: typer.Context,
    since: str = typer.Option(
        "24h",
        "--since",
        help="Window for exceeded-event counts (usage/limit always show the "
        "latest data point).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Show quota usage."""
    from getop.auth import get_clients

    state = ctx.obj
    clients = get_clients(state.project, state.location, getattr(state, "quota_project", None))

    try:
        if err_console.is_terminal:
            with err_console.status("Querying quota metrics…", spinner="dots"):
                rows = collect_quota(clients, since=since)
        else:
            rows = collect_quota(clients, since=since)
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    except gexceptions.PermissionDenied as exc:
        err_console.print(
            "[red]Permission denied querying Cloud Monitoring.[/red] "
            "Grant the caller roles/monitoring.viewer on the project and retry.\n"
            f"[dim]{exc}[/dim]"
        )
        raise typer.Exit(code=1)

    output(
        rows,
        _render_quota(
            rows, since, f"{clients.project} ({clients.location})", clients.project
        ),
        as_json,
    )


def stats_command(
    ctx: typer.Context,
    engine: Optional[str] = typer.Option(
        None,
        "--engine",
        help="Restrict to this engine ID, when the discovered metric exposes an "
        "engine-identifying label (otherwise the filter is skipped for that metric).",
    ),
    since: str = typer.Option(
        "24h",
        "--since",
        help="Look-back window, e.g. 30m, 24h, 7d.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Show usage metrics."""
    from getop.auth import get_clients

    state = ctx.obj
    clients = get_clients(state.project, state.location, getattr(state, "quota_project", None))

    try:
        # Single-line spinner on stderr while the (parallelised) metric walk
        # runs; stdout stays clean for tables/--json.
        if err_console.is_terminal:
            with err_console.status("Discovering metrics…", spinner="dots") as status:

                def show_progress(metric_type: str, done: int, total: int) -> None:
                    short = metric_type.removeprefix("discoveryengine.googleapis.com/")
                    status.update(f"[{done}/{total}] {short}")

                data = collect_stats(
                    clients, since=since, engine=engine, progress=show_progress
                )
        else:
            data = collect_stats(clients, since=since, engine=engine)
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    except gexceptions.PermissionDenied as exc:
        err_console.print(
            "[red]Permission denied querying Cloud Monitoring.[/red] "
            "Grant the caller roles/monitoring.viewer on the project and retry.\n"
            f"[dim]{exc}[/dim]"
        )
        raise typer.Exit(code=1)

    header = f"{clients.project} ({clients.location})"
    if engine:
        header += f", engine {engine}"
    output(data, _render(data, header), as_json)
