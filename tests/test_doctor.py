"""Status logic for getop doctor with all collectors monkeypatched."""

from types import SimpleNamespace

from getop.commands import doctor


def _fake_clients():
    return SimpleNamespace(project="p", location="global")


def _patch_all_ok(monkeypatch):
    monkeypatch.setattr(
        doctor.ls_cmd, "collect_engines", lambda c: [{"id": "e-1"}]
    )
    monkeypatch.setattr(
        doctor.ls_cmd, "collect_datastores", lambda c: [{"id": "ds-1"}]
    )
    monkeypatch.setattr(
        doctor.ls_cmd,
        "collect_connectors",
        lambda c: [{"data_source": "google_drive", "state": "ACTIVE", "last_sync_time": None}],
    )
    monkeypatch.setattr(
        doctor.ls_cmd, "collect_agents", lambda c: [{"engine_id": "e-1", "id": "a-1"}]
    )
    monkeypatch.setattr(doctor.logs_cmd, "collect_entries", lambda c, f, limit: [])
    monkeypatch.setattr(
        doctor.stats_cmd,
        "collect_stats",
        lambda c, since, categories=None: {
            "metrics_discovered": ["m1"],
            "connector": {"freshest_point": None},
        },
    )


def test_all_ok(monkeypatch):
    _patch_all_ok(monkeypatch)
    results = doctor.run_doctor(_fake_clients(), "24h")
    assert len(results) == 7
    assert all(r["status"] == doctor.OK for r in results)


def test_connector_error_logs_fail(monkeypatch):
    _patch_all_ok(monkeypatch)

    def entries(clients, filter_str, limit):
        if "connector_activity" in filter_str:
            return [{"timestamp": "t", "severity": "ERROR"}]
        return []

    monkeypatch.setattr(doctor.logs_cmd, "collect_entries", entries)
    results = doctor.run_doctor(_fake_clients(), "24h")
    by_check = {r["check"]: r["status"] for r in results}
    assert by_check["connector errors (24h)"] == doctor.FAIL


def test_inactive_connector_warns(monkeypatch):
    _patch_all_ok(monkeypatch)
    monkeypatch.setattr(
        doctor.ls_cmd,
        "collect_connectors",
        lambda c: [{"data_source": "jira", "state": "FAILED", "last_sync_time": None}],
    )
    results = doctor.run_doctor(_fake_clients(), "24h")
    by_check = {r["check"]: r for r in results}
    row = by_check["data connectors"]
    assert row["status"] == doctor.WARN
    assert "jira state=FAILED" in row["detail"]


def test_check_exception_becomes_fail_and_others_run(monkeypatch):
    _patch_all_ok(monkeypatch)

    def boom(clients):
        raise RuntimeError("kaput")

    monkeypatch.setattr(doctor.ls_cmd, "collect_engines", boom)
    results = doctor.run_doctor(_fake_clients(), "24h")
    by_check = {r["check"]: r for r in results}
    assert by_check["engines"]["status"] == doctor.FAIL
    assert "kaput" in by_check["engines"]["detail"]
    assert by_check["datastores"]["status"] == doctor.OK
