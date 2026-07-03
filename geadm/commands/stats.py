"""`geadm stats` — query volume, latency and connector sync freshness.

Reads Cloud Monitoring time series for discoveryengine.googleapis.com
metrics. Strictly read-only (list_metric_descriptors / list_time_series
only) — needs only roles/monitoring.viewer on the target project.

The set of published discoveryengine.googleapis.com metrics is not
reliably documented, so this command always discovers what exists at
runtime via list_metric_descriptors before querying any values; it never
assumes a specific metric type is present.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import typer
from google.api import metric_pb2
from google.api_core import exceptions as gexceptions
from google.cloud import monitoring_v3
from rich.console import Group
from rich.text import Text

from geadm.duration import since_timestamp
from geadm.render import err_console, output, table

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
) -> dict:
    """Discover discoveryengine metrics and summarise volume/latency/connector sync.

    Pure, read-only: list_metric_descriptors + list_time_series only.
    Raises ValueError if `since` is malformed, and propagates any
    google.api_core.exceptions raised by the API (e.g. PermissionDenied)
    so the caller can render a clean error.

    `categories` restricts which metric categories ("query_volume", "latency",
    "connector") get per-metric time-series queries; discovery still lists
    everything. None means all categories (the default for `geadm stats`).
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

    connector_latest: Optional[str] = None
    alignment_seconds = 300

    for descriptor in descriptors:
        category = _category_for(descriptor.type, descriptor.value_type)
        if categories is not None and category not in categories:
            continue
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

        summary = {
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

        if category == "connector":
            result["connector"]["metrics"][descriptor.type] = summary
            if latest_iso and (connector_latest is None or latest_iso > connector_latest):
                connector_latest = latest_iso
        elif category == "latency":
            result["latency"][descriptor.type] = summary
        else:
            result["query_volume"][descriptor.type] = summary

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


def _render(data: dict) -> Any:
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
            agg = "-" if m["aggregate"] is None else f"{m['aggregate']:.3g} ({m['aggregate_label']})"
            rows.append((m["type"], m["category"], m["points"], agg, latest))

    t = table(
        "Discovery Engine metrics",
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
    """Query volume, latency and connector sync freshness for Gemini Enterprise.

    Discovers discoveryengine.googleapis.com metric descriptors at runtime
    (the published metric set is not reliably documented), then summarises
    request/query counts, latency, and the freshest connector sync signal
    over the --since window. Read-only: needs only roles/monitoring.viewer.
    """
    from geadm.auth import get_clients

    state = ctx.obj
    clients = get_clients(state.project, state.location, getattr(state, "quota_project", None))

    try:
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

    output(data, _render(data), as_json)
