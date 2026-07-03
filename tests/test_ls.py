"""Collector behaviour for geadm ls against fake clients."""

from conftest import FakeClients, engine, user_license

from geadm.commands import ls


def test_collect_engines_shapes_dicts():
    clients = FakeClients(engines=[engine("e-1", "My App")])
    rows = ls.collect_engines(clients)
    assert rows == [
        {
            "id": "e-1",
            "name": "projects/p/locations/global/collections/default_collection/engines/e-1",
            "display_name": "My App",
            "solution_type": "SOLUTION_TYPE_SEARCH",
            "industry_vertical": "GENERIC",
            "data_store_ids": ["ds-1"],
            "create_time": None,
        }
    ]


def test_collect_connector_none_on_404():
    clients = FakeClients(rest_responses={"dataConnector": 404})
    assert ls.collect_connector(clients) is None


def test_collect_connectors_scans_all_collections():
    clients = FakeClients(
        rest_responses={
            "/collections?": {},  # never matched; rest_get matches by substring
            "collections/coll-a/dataConnector": {
                "name": "projects/p/locations/global/collections/coll-a/dataConnector",
                "dataSource": "google_drive",
                "state": "ACTIVE",
                "refreshInterval": "0s",
                "entities": [{"entityName": "file", "dataStore": "ds"}],
            },
            "collections/default_collection/dataConnector": 404,
            "/collections": {
                "collections": [
                    {"name": "projects/p/locations/global/collections/coll-a"},
                    {
                        "name": "projects/p/locations/global/collections/default_collection"
                    },
                ]
            },
        }
    )
    rows = ls.collect_connectors(clients)
    assert len(rows) == 1
    row = rows[0]
    assert row["collection"] == "coll-a"
    assert row["data_source"] == "google_drive"
    assert row["entities"] == [{"entity_name": "file", "data_store": "ds"}]


def test_collect_agents_reports_empty_engines_as_note():
    clients = FakeClients(
        engines=[engine("e-1")],
        rest_responses={"assistants/default_assistant/agents": 404},
    )
    rows = ls.collect_agents(clients)
    assert len(rows) == 1
    assert rows[0]["engine_id"] == "e-1"
    assert rows[0].get("note")


def test_collect_agents_flattens_agent_fields():
    clients = FakeClients(
        engines=[engine("e-1")],
        rest_responses={
            "assistants/default_assistant/agents": {
                "agents": [
                    {
                        "name": "projects/p/.../agents/agent-1",
                        "displayName": "Deep Research",
                        "state": "ENABLED",
                    }
                ]
            }
        },
    )
    rows = ls.collect_agents(clients)
    assert rows[0]["id"] == "agent-1"
    assert rows[0]["display_name"] == "Deep Research"
    assert rows[0]["state"] == "ENABLED"


def test_collect_licenses_shapes_dicts():
    clients = FakeClients(
        user_licenses=[user_license("alice@example.com", "ASSIGNED", "gemini-business")]
    )
    rows = ls.collect_licenses(clients)
    assert rows == [
        {
            "user_principal": "alice@example.com",
            "user_profile": None,
            "license_assignment_state": "ASSIGNED",
            "license_config": (
                "projects/p/locations/global/userStores/default_user_store"
                "/licenseConfigs/gemini-business"
            ),
            "license_config_id": "gemini-business",
            "create_time": None,
            "update_time": None,
            "last_login_time": None,
        }
    ]


def test_collect_licenses_empty():
    clients = FakeClients(user_licenses=[])
    assert ls.collect_licenses(clients) == []


def test_licenses_command_reports_no_user_store_on_not_found(app_runner):
    from google.api_core import exceptions as gexceptions

    from geadm.main import app

    def fake_get_clients(project, location, quota_project=None):
        clients = FakeClients()

        def raise_not_found(*args, **kwargs):
            raise gexceptions.NotFound("no user store")

        clients._discovery.list_user_licenses = raise_not_found
        return clients

    import geadm.commands.ls as ls_module

    original = ls_module.get_clients
    ls_module.get_clients = fake_get_clients
    try:
        result = app_runner.invoke(app, ["ls", "licenses"])
    finally:
        ls_module.get_clients = original

    assert result.exit_code == 0
    assert "no user store found" in result.stdout
