"""collect/parse and rendering for `getop config` (app-level configuration)."""

from io import StringIO

from conftest import FakeClients, engine
from rich.console import Console

from getop.commands import config


_FULL_ENGINE = {
    "name": "projects/p/locations/global/collections/default_collection/engines/e-1",
    "displayName": "My App",
    "createTime": "2025-11-02T10:00:00Z",
    "appType": "APP_TYPE_INTRANET",
    "observabilityConfig": {"observabilityEnabled": True},  # sensitive omitted → False
    "knowledgeGraphConfig": {"enableCloudKnowledgeGraph": True},
    "marketplaceAgentVisibility": "MARKETPLACE_PUBLIC",
    "commonConfig": {"companyName": "Acme"},
    "features": {
        "model-selector": "FEATURE_STATE_ON",
        "disable-image-generation": "FEATURE_STATE_ON",
    },
}


def _render(renderable) -> str:
    console = Console(file=StringIO(), width=160, force_terminal=False)
    console.print(renderable)
    return console.file.getvalue()


def test_collect_parses_curated_fields():
    clients = FakeClients(
        engines=[engine("e-1", "My App")], rest_responses={"engines/e-1": _FULL_ENGINE}
    )
    data = config.collect_engine_configs(clients)
    row = data["engines"][0]
    assert row["app_type"] == "APP_TYPE_INTRANET"
    assert row["observability_enabled"] is True
    assert row["cloud_knowledge_graph_enabled"] is True
    assert row["private_knowledge_graph_enabled"] is False
    assert row["marketplace_agent_visibility"] == "MARKETPLACE_PUBLIC"
    assert row["company_name"] == "Acme"
    assert row["features_normalized"] == {
        "model-selector": True,
        "image-generation": False,
    }
    assert data["errors"] == {}


def test_absent_config_objects_are_none():
    clients = FakeClients(
        engines=[engine("e-1")],
        rest_responses={"engines/e-1": {"appType": "APP_TYPE_INTRANET"}},
    )
    row = config.collect_engine_configs(clients)["engines"][0]
    assert row["observability_enabled"] is None
    assert row["sensitive_logging_enabled"] is None
    assert row["cloud_knowledge_graph_enabled"] is None
    assert row["private_knowledge_graph_enabled"] is None


def test_present_parent_absent_bool_is_false():
    # proto3 REST omits false booleans: the parent's presence means "known off".
    row = config.parse_engine_config({"observabilityConfig": {"observabilityEnabled": True}})
    assert row["sensitive_logging_enabled"] is False


def test_per_engine_fetch_error_is_isolated():
    clients = FakeClients(
        engines=[engine("e-1", "Good"), engine("e-2", "Denied")],
        rest_responses={"engines/e-1": _FULL_ENGINE, "engines/e-2": 403},
    )
    data = config.collect_engine_configs(clients)
    good, denied = data["engines"]
    assert good["observability_enabled"] is True
    assert "error" not in good
    assert "HTTPError" in denied["error"]
    assert "e-2" in data["errors"]


def test_single_engine_direct_get():
    clients = FakeClients(rest_responses={"engines/e-1": _FULL_ENGINE})
    data = config.collect_engine_configs(clients, "e-1")
    assert len(clients.rest_calls) == 1
    assert clients.rest_calls[0].startswith("v1alpha/")
    assert clients.rest_calls[0].endswith("engines/e-1")
    row = data["engines"][0]
    assert row["id"] == "e-1"
    assert row["display_name"] == "My App"


def test_single_engine_not_found_exits_1(app_runner, monkeypatch):
    from getop.commands import config as config_mod
    from getop.main import app

    monkeypatch.setattr(
        config_mod, "get_clients", lambda *a, **k: FakeClients(rest_responses={})
    )
    result = app_runner.invoke(app, ["config", "missing-app"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_render_table_smoke():
    clients = FakeClients(
        engines=[engine("e-1", "My App")],
        rest_responses={
            "engines/e-1": {
                **_FULL_ENGINE,
                "observabilityConfig": {"sensitiveLoggingEnabled": True},
            }
        },
    )
    out = _render(config._render_table(config.collect_engine_configs(clients)))
    assert "My App" in out
    assert "Prompt logging" in out
    assert "⚠ ON" in out


def test_render_card_smoke():
    clients = FakeClients(
        rest_responses={
            "engines/e-1": {
                **_FULL_ENGINE,
                "observabilityConfig": {"sensitiveLoggingEnabled": True},
            }
        },
    )
    data = config.collect_engine_configs(clients, "e-1")
    out = _render(config._render_card(data["engines"][0]))
    assert "My App" in out
    assert "Features (1 on · 1 off)" in out
    assert "⚠ ON" in out
    assert "Agent gallery visibility: MARKETPLACE_PUBLIC" in out


def test_render_card_unknown_config_shows_dash_not_off():
    row = {
        "id": "e-1",
        "display_name": "Old App",
        **config.parse_engine_config({}),
    }
    out = _render(config._render_card(row))
    assert "Traces/logs export: —" in out
    assert "off" not in out.split("Observability")[1].split("Knowledge graph")[0]
