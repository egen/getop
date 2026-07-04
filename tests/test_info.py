"""collect_info aggregation and connector/datastore mapping for getop info."""

from conftest import FakeClients, engine, user_license

from getop.commands import info


def _clients():
    return FakeClients(
        engines=[engine("e-1", "My App")],
        rest_responses={
            "collections/coll-a/dataConnector": {
                "name": "projects/p/locations/global/collections/coll-a/dataConnector",
                "dataSource": "google_drive",
                "state": "ACTIVE",
                "entities": [{"entityName": "file", "data_store": None,
                              "dataStore": "projects/p/locations/global/collections/coll-a/dataStores/ds-1"}],
            },
            "collections/default_collection/dataConnector": 404,
            "assistants/default_assistant/agents": {
                "agents": [{"name": "x/agents/a-1", "displayName": "Deep Research", "state": "ENABLED"}]
            },
            # must precede "/collections": FakeClients.rest_get matches by
            # substring in insertion order, and the engine-detail path also
            # contains "/collections".
            "engines/e-1": {
                "name": "projects/p/locations/global/collections/default_collection/engines/e-1",
                "appType": "APP_TYPE_INTRANET",
                "features": {
                    "model-selector": "FEATURE_STATE_ON",
                    "disable-image-generation": "FEATURE_STATE_ON",
                    "disable-agent-sharing": "FEATURE_STATE_OFF",
                },
            },
            "/collections": {
                "collections": [
                    {"name": "projects/p/locations/global/collections/coll-a"},
                    {"name": "projects/p/locations/global/collections/default_collection"},
                ]
            },
            "licenseConfigs/cfg": {
                "name": "projects/p/locations/global/licenseConfigs/cfg",
                "licenseCount": "419",
                "subscriptionTier": "SUBSCRIPTION_TIER_ENTERPRISE",
                "state": "ACTIVE",
            },
        },
        user_licenses=[user_license("alice@example.com", "ASSIGNED", "cfg")],
    )


def test_collect_info_aggregates_all_collectors():
    data = info.collect_info(_clients())
    assert data["project"] == "test-project"
    assert [e["id"] for e in data["engines"]] == ["e-1"]
    assert len(data["connectors"]) == 1
    assert data["agents"][0]["display_name"] == "Deep Research"
    assert data["licenses"][0]["user_principal"] == "alice@example.com"
    assert data["errors"] == {}


def test_collect_info_survives_collector_failure():
    clients = _clients()

    def boom(*a, **k):
        raise RuntimeError("kaput")

    clients._discovery.list_user_licenses = boom
    data = info.collect_info(clients)
    assert data["licenses"] == []
    assert "kaput" in data["errors"]["licenses"]
    assert data["engines"]  # other collectors unaffected


def test_connector_by_datastore_maps_entity_stores():
    connectors = [
        {
            "data_source": "google_drive",
            "state": "ACTIVE",
            "entities": [
                {"entity_name": "file", "data_store": "projects/p/.../dataStores/ds-1"}
            ],
        }
    ]
    mapping = info._connector_by_datastore(connectors)
    assert mapping["ds-1"]["data_source"] == "google_drive"


def test_collect_info_fetches_license_configs_for_seats():
    data = info.collect_info(_clients())
    configs = data["license_configs"]
    assert len(configs) == 1
    assert configs[0]["license_count"] == 419
    assert configs[0]["state"] == "ACTIVE"


def test_normalize_features_inverts_disable_prefix():
    normalized = info.normalize_features(
        {
            "model-selector": "FEATURE_STATE_ON",
            "disable-image-generation": "FEATURE_STATE_ON",
            "disable-agent-sharing": "FEATURE_STATE_OFF",
            "session-sharing": "FEATURE_STATE_OFF",
        }
    )
    assert normalized == {
        "model-selector": True,
        "image-generation": False,
        "agent-sharing": True,
        "session-sharing": False,
    }


def test_collect_info_attaches_engine_features():
    data = info.collect_info(_clients())
    engine_row = data["engines"][0]
    assert engine_row["app_type"] == "APP_TYPE_INTRANET"
    assert engine_row["features"]["model-selector"] == "FEATURE_STATE_ON"


def test_wrap_names_chunks_lines():
    names = [f"feature-{i}" for i in range(10)]
    lines = info._wrap_names(names, "  ", width=30)
    assert all(len(line) <= 34 for line in lines)
    assert " ".join(l.strip() for l in lines) == " ".join(names)


def _agent(name, state="PRIVATE"):
    return {"display_name": name, "state": state, "engine_id": "e-1", "id": name}


def test_agent_lines_groups_my_agent_defaults():
    agents = [_agent("My Agent") for _ in range(12)] + [
        _agent("Deep Research", "ENABLED"),
        _agent("FDD"),
    ]
    lines = info._agent_lines(agents)
    joined = "\n".join(lines)
    assert "My Agent [dim]×12 (user defaults, private)" in joined
    assert joined.count("My Agent") == 1  # never listed individually
    assert lines[0].startswith("  • Deep Research")  # ENABLED sorts first


def test_agent_lines_truncates_and_counts_duplicates():
    agents = [_agent(f"Agent {i}") for i in range(15)] + [_agent("Dup"), _agent("Dup")]
    lines = info._agent_lines(agents, max_rows=5)
    assert len([l for l in lines if l.startswith("  •")]) == 5
    assert any("+12 more" in l for l in lines)
    dup_line = next(l for l in info._agent_lines(agents, max_rows=20) if "Dup" in l)
    assert "×2" in dup_line


def test_agent_lines_empty():
    assert info._agent_lines([]) == ["  [dim]none[/dim]"]


def test_render_info_smoke():
    data = info.collect_info(_clients())
    group = info._render_info(data)
    from rich.console import Console
    from io import StringIO

    console = Console(file=StringIO(), width=140, force_terminal=False)
    console.print(group)
    out = console.file.getvalue()
    assert "My App" in out
    assert "Engines" in out
    assert "Deep Research" in out
