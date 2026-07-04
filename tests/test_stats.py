"""Formatting, categorisation and quota/stat collection with a fake monitoring client."""

from datetime import datetime, timezone
from types import SimpleNamespace

from google.api import metric_pb2
from google.cloud import monitoring_v3

from getop.commands import stats

MD = metric_pb2.MetricDescriptor


# ---- number formatting ---------------------------------------------------------


def test_fmt_number_no_scientific_notation():
    assert stats._fmt_number(10_000_000) == "10,000,000"
    assert stats._fmt_number(9.22e18) == "unlimited"
    assert stats._fmt_number(76.0) == "76"
    assert stats._fmt_number(1.234) == "1.23"
    assert stats._fmt_number(0.0421) == "0.0421"
    assert stats._fmt_number(None) == "-"


def test_fmt_bytes():
    assert stats._fmt_bytes(89_700_000) == "85.5 MiB"
    assert stats._fmt_bytes(1_073_741_824) == "1.0 GiB"
    assert stats._fmt_bytes(512) == "512 B"
    assert stats._fmt_bytes(9.3e18) == "unlimited"


def test_category_for():
    assert stats._category_for("x/dataconnector/request_count", MD.ValueType.INT64) == "connector"
    assert stats._category_for("x/total_latencies", MD.ValueType.INT64) == "latency"
    assert stats._category_for("x/request_count", MD.ValueType.DISTRIBUTION) == "latency"
    assert stats._category_for("x/request_count", MD.ValueType.INT64) == "volume"


# ---- fakes ---------------------------------------------------------------------


def _descriptor(metric_type: str, kind=MD.MetricKind.GAUGE, value=MD.ValueType.INT64):
    return MD(type=metric_type, metric_kind=kind, value_type=value)


def _series(values: list[int], resource_labels: dict | None = None, metric_labels=None):
    now = datetime.now(timezone.utc)
    return monitoring_v3.TimeSeries(
        resource={"type": "discoveryengine.googleapis.com/Location",
                  "labels": resource_labels or {"location": "global"}},
        metric={"type": "x", "labels": metric_labels or {}},
        points=[
            monitoring_v3.Point(
                interval={"end_time": now},
                value={"int64_value": v},
            )
            for v in values
        ],
    )


class FakeMonitoring:
    def __init__(self, descriptors, series_by_type):
        self._descriptors = descriptors
        self._series_by_type = series_by_type

    def list_metric_descriptors(self, request):
        return list(self._descriptors)

    def list_time_series(self, request):
        for metric_type, series in self._series_by_type.items():
            if f'"{metric_type}"' in request.filter:
                return list(series)
        return []


def _clients(descriptors, series_by_type):
    return SimpleNamespace(
        monitoring=FakeMonitoring(descriptors, series_by_type),
        monitoring_project_path="projects/test",
    )


# ---- collect_stats -------------------------------------------------------------


def test_collect_stats_buckets_and_sums():
    clients = _clients(
        [
            _descriptor("d.com/request_count", kind=MD.MetricKind.DELTA),
            _descriptor("d.com/dataconnector/request_count"),
        ],
        {
            "d.com/request_count": [_series([3, 4])],
            "d.com/dataconnector/request_count": [_series([1])],
        },
    )
    data = stats.collect_stats(clients, since="1h")
    assert set(data["metrics_discovered"]) == {
        "d.com/request_count",
        "d.com/dataconnector/request_count",
    }
    assert data["query_volume"]["d.com/request_count"]["aggregate"] == 7
    assert data["connector"]["freshest_point"] is not None


def test_collect_stats_categories_filter_skips_queries():
    clients = _clients(
        [
            _descriptor("d.com/request_count", kind=MD.MetricKind.DELTA),
            _descriptor("d.com/dataconnector/request_count"),
        ],
        {
            "d.com/request_count": [_series([3])],
            "d.com/dataconnector/request_count": [_series([1])],
        },
    )
    data = stats.collect_stats(clients, since="1h", categories={"connector"})
    assert len(data["metrics_discovered"]) == 2  # discovery still lists all
    assert data["query_volume"] == {}  # but only connector metrics were queried
    assert data["connector"]["metrics"]


def test_collect_stats_progress_callback():
    clients = _clients(
        [_descriptor("d.com/request_count", kind=MD.MetricKind.DELTA)],
        {"d.com/request_count": [_series([3])]},
    )
    calls = []
    stats.collect_stats(
        clients, since="1h", progress=lambda t, d, n: calls.append((t, d, n))
    )
    assert calls == [("d.com/request_count", 1, 1)]


# ---- collect_quota -------------------------------------------------------------


def test_collect_quota_pairs_usage_and_limit():
    clients = _clients(
        [
            _descriptor("d.com/quota/documents_regional/usage"),
            _descriptor("d.com/quota/documents_regional/limit"),
            _descriptor("d.com/quota/documents_regional/exceeded", kind=MD.MetricKind.DELTA),
        ],
        {
            "d.com/quota/documents_regional/usage": [_series([76])],
            "d.com/quota/documents_regional/limit": [_series([10_000_000])],
            "d.com/quota/documents_regional/exceeded": [_series([2, 1])],
        },
    )
    rows = stats.collect_quota(clients, since="24h")
    assert len(rows) == 1
    row = rows[0]
    assert row["quota"] == "documents_regional"
    assert row["location"] == "global"
    assert row["usage"] == 76
    assert row["limit"] == 10_000_000
    assert row["exceeded"] == 3
    assert row["percent_used"] == 0.0


def test_quota_console_url():
    url = stats._quota_console_url("my-proj")
    assert "project=my-proj" in url
    assert "service=discoveryengine.googleapis.com" in url
    assert url.startswith("https://console.cloud.google.com/iam-admin/quotas")


def test_render_quota_links_names_but_json_stays_plain():
    from rich.console import Console
    from io import StringIO

    rows = [
        {
            "quota": "documents_regional",
            "location": "global",
            "usage": 76,
            "limit": 10_000_000,
            "percent_used": 0.0,
            "exceeded": 0,
        }
    ]
    renderable = stats._render_quota(rows, "24h", "my-proj (global)", "my-proj")
    console = Console(file=StringIO(), width=200, force_terminal=True)
    console.print(renderable)
    out = console.file.getvalue()
    # the OSC 8 hyperlink escape carries the console URL
    assert "iam-admin/quotas" in out
    # raw row dict (what --json emits) never gained link markup
    assert rows[0]["quota"] == "documents_regional"


def test_collect_quota_unlimited_has_no_percent():
    clients = _clients(
        [
            _descriptor("d.com/quota/size_tier/usage"),
            _descriptor("d.com/quota/size_tier/limit"),
        ],
        {
            "d.com/quota/size_tier/usage": [_series([100])],
            "d.com/quota/size_tier/limit": [_series([9_223_372_036_854_775_807])],
        },
    )
    rows = stats.collect_quota(clients, since="24h")
    assert rows[0]["percent_used"] is None
