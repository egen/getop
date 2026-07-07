"""CLI wiring smoke tests: every command exposes working --help."""

import pytest

from getop.main import app


@pytest.mark.parametrize(
    "args",
    [
        ["--help"],
        ["ls", "--help"],
        ["ls", "engines", "--help"],
        ["ls", "datastores", "--help"],
        ["ls", "connectors", "--help"],
        ["ls", "agents", "--help"],
        ["logs", "--help"],
        ["logs", "connector", "--help"],
        ["logs", "user", "--help"],
        ["stats", "--help"],
        ["quota", "--help"],
        ["doctor", "--help"],
        ["config", "--help"],
        ["top", "--help"],
    ],
)
def test_help_screens(app_runner, args):
    result = app_runner.invoke(app, args)
    assert result.exit_code == 0, result.output


def test_help_is_concise_no_iam_or_api_internals(app_runner):
    """Help output reads like a typical CLI: IAM roles, RPC names and API
    versions belong in the README, not --help."""
    combined = ""
    for args in (
        ["--help"],
        ["ls", "--help"],
        ["logs", "--help"],
        ["stats", "--help"],
        ["quota", "--help"],
        ["doctor", "--help"],
        ["info", "--help"],
        ["config", "--help"],
        ["top", "--help"],
    ):
        combined += app_runner.invoke(app, args).output
    for banned in ("roles/", "ServiceClient", "v1alpha", "read-only ("):
        assert banned not in combined, f"{banned!r} leaked into help output"


def test_version_command(app_runner):
    result = app_runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "getop " in result.output
    assert "commit" in result.output


def test_version_json(app_runner):
    import json

    result = app_runner.invoke(app, ["version", "--json"])
    data = json.loads(result.output)
    assert set(data) == {"version", "tag", "commit"}
    assert data["tag"] == f"v{data['version']}"


def test_update_check_reports_when_current(app_runner, monkeypatch):
    from getop import main

    monkeypatch.setattr(main, "_installed_version", lambda: "9.9.9")
    monkeypatch.setattr(main, "_latest_version", lambda *a, **k: "9.9.9")
    result = app_runner.invoke(app, ["update", "--check"])
    assert result.exit_code == 0
    assert "up to date" in result.output


def test_update_check_reports_when_outdated(app_runner, monkeypatch):
    from getop import main

    monkeypatch.setattr(main, "_installed_version", lambda: "0.1.0")
    monkeypatch.setattr(main, "_latest_version", lambda *a, **k: "0.2.0")
    result = app_runner.invoke(app, ["update", "--check"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output and "0.2.0" in result.output


def test_update_offline_fails_cleanly(app_runner, monkeypatch):
    from getop import main

    monkeypatch.setattr(main, "_latest_version", lambda *a, **k: None)
    result = app_runner.invoke(app, ["update", "--check"])
    assert result.exit_code == 1


def test_update_json(app_runner, monkeypatch):
    import json

    from getop import main

    monkeypatch.setattr(main, "_installed_version", lambda: "0.1.0")
    monkeypatch.setattr(main, "_latest_version", lambda *a, **k: "0.2.0")
    result = app_runner.invoke(app, ["update", "--json"])
    data = json.loads(result.output)
    assert data["current"] == "0.1.0"
    assert data["latest"] == "0.2.0"
    assert data["outdated"] is True


def test_local_install_source_reads_direct_url(monkeypatch):
    """PEP 610: a path install carries direct_url.json; PyPI installs don't."""
    import importlib.metadata
    from types import SimpleNamespace

    from getop.main import _local_install_source

    def fake_distribution(payload):
        return lambda name: SimpleNamespace(read_text=lambda f: payload)

    monkeypatch.setattr(
        importlib.metadata,
        "distribution",
        fake_distribution('{"url": "file:///Users/x/src/getop", "dir_info": {}}'),
    )
    assert _local_install_source() == "/Users/x/src/getop"

    monkeypatch.setattr(importlib.metadata, "distribution", fake_distribution(None))
    assert _local_install_source() is None

    monkeypatch.setattr(importlib.metadata, "distribution", fake_distribution("{not json"))
    assert _local_install_source() is None


def test_update_blocked_by_local_source(app_runner, monkeypatch):
    """The issue #56 scenario: spec points at a checkout, so upgrading would
    no-op against PyPI — update must say so instead of blaming propagation."""
    from getop import main

    monkeypatch.setattr(main, "_installed_version", lambda: "1.1.0")
    monkeypatch.setattr(main, "_latest_version", lambda *a, **k: "1.2.0")
    monkeypatch.setattr(main, "_local_install_source", lambda: "/Users/x/src/getop")
    result = app_runner.invoke(app, ["update"])
    assert result.exit_code == 1
    assert "not PyPI" in result.output
    assert "/Users/x/src/getop" in result.output
    assert "propagating" not in result.output


def test_update_check_with_local_source_reports_and_exits_zero(app_runner, monkeypatch):
    from getop import main

    monkeypatch.setattr(main, "_installed_version", lambda: "1.1.0")
    monkeypatch.setattr(main, "_latest_version", lambda *a, **k: "1.2.0")
    monkeypatch.setattr(main, "_local_install_source", lambda: "/Users/x/src/getop")
    result = app_runner.invoke(app, ["update", "--check"])
    assert result.exit_code == 0
    assert "not PyPI" in result.output


def test_update_json_includes_local_source(app_runner, monkeypatch):
    import json

    from getop import main

    monkeypatch.setattr(main, "_installed_version", lambda: "1.1.0")
    monkeypatch.setattr(main, "_latest_version", lambda *a, **k: "1.2.0")
    monkeypatch.setattr(main, "_local_install_source", lambda: "/Users/x/src/getop")
    result = app_runner.invoke(app, ["update", "--json"])
    assert json.loads(result.output)["local_source"] == "/Users/x/src/getop"


def test_reinstall_hint_per_method():
    from getop.main import _reinstall_hint

    assert _reinstall_hint("pipx") == "pipx uninstall getop && pipx install getop"
    assert _reinstall_hint("uv tool") == "uv tool install --force getop"
    assert "pip install --upgrade getop" in _reinstall_hint("pip")


def test_install_method_forces_fresh_index():
    """update must bypass the client index cache, or it no-ops right after a
    release when the cached index still lists the old version."""
    from getop.main import _install_method

    _, argv = _install_method()
    joined = " ".join(argv)
    assert "no-cache" in joined


def test_version_tuple_compares():
    from getop.main import _version_tuple

    assert _version_tuple("0.2.0") > _version_tuple("0.1.9")
    assert _version_tuple("0.10.0") > _version_tuple("0.9.0")


def test_upgrade_succeeded_by_version_reached():
    from getop.main import _upgrade_succeeded

    # installed reached latest, even if output is empty/stale
    assert _upgrade_succeeded("", "0.5.0", "0.5.0") is True


def test_upgrade_succeeded_by_marker_when_version_read_stale():
    from getop.main import _upgrade_succeeded

    # running process still reads old version, but pipx confirmed the upgrade
    assert _upgrade_succeeded(
        "upgraded package getop from 0.4.0 to 0.5.0", "0.4.0", "0.5.0"
    ) is True


def test_upgrade_noop_is_not_success():
    from getop.main import _upgrade_succeeded

    # the reported propagation-lag case: pipx no-op'd, version didn't move
    assert _upgrade_succeeded(
        "getop is already at latest version 0.4.0", "0.4.0", "0.5.0"
    ) is False


def test_logs_user_help_mentions_prompt_logging(app_runner):
    out = app_runner.invoke(app, ["logs", "user", "--help"]).output
    assert "prompt/response" in out
