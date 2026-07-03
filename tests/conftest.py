"""Shared offline test doubles — no GCP access anywhere in the suite."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import requests


def http_error(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(response=response)


class FakeDiscoveryClient:
    def __init__(
        self,
        engines: list | None = None,
        data_stores: list | None = None,
        user_licenses: list | Exception | None = None,
    ):
        self._engines = engines or []
        self._data_stores = data_stores or []
        self._user_licenses = user_licenses if user_licenses is not None else []

    def list_engines(self, parent: str):
        return list(self._engines)

    def list_data_stores(self, parent: str):
        return list(self._data_stores)

    def list_user_licenses(self, parent: str):
        if isinstance(self._user_licenses, Exception):
            raise self._user_licenses
        return list(self._user_licenses)


class FakeClients:
    """Duck-type of geadm.auth.Clients for collector tests."""

    def __init__(
        self,
        engines: list | None = None,
        data_stores: list | None = None,
        rest_responses: dict[str, Any] | None = None,
        log_entries: list | None = None,
        user_licenses: list | Exception | None = None,
    ):
        self.project = "test-project"
        self.location = "global"
        self._discovery = FakeDiscoveryClient(engines, data_stores, user_licenses)
        # path -> dict response, int -> raise HTTPError(status)
        self._rest = rest_responses or {}
        self.rest_calls: list[str] = []
        self.logging = SimpleNamespace(
            list_entries=lambda **kwargs: list(log_entries or [])
        )

    @property
    def collection_path(self) -> str:
        return (
            f"projects/{self.project}/locations/{self.location}"
            "/collections/default_collection"
        )

    @property
    def monitoring_project_path(self) -> str:
        return f"projects/{self.project}"

    def discoveryengine(self, client_cls: type) -> Any:
        return self._discovery

    def rest_get(self, path: str, params=None, host=None) -> dict:
        self.rest_calls.append(path)
        for key, value in self._rest.items():
            if key in path:
                if isinstance(value, int):
                    raise http_error(value)
                return value
        raise http_error(404)


def engine(engine_id: str, display_name: str = "An Engine") -> SimpleNamespace:
    return SimpleNamespace(
        name=f"projects/p/locations/global/collections/default_collection/engines/{engine_id}",
        display_name=display_name,
        solution_type=SimpleNamespace(name="SOLUTION_TYPE_SEARCH"),
        industry_vertical=SimpleNamespace(name="GENERIC"),
        data_store_ids=["ds-1"],
        create_time=None,
    )


def user_license(
    user_principal: str = "user@example.com",
    state_name: str = "ASSIGNED",
    config_id: str = "gemini-business",
) -> SimpleNamespace:
    return SimpleNamespace(
        user_principal=user_principal,
        user_profile=None,
        license_assignment_state=SimpleNamespace(name=state_name),
        license_config=(
            "projects/p/locations/global/userStores/default_user_store"
            f"/licenseConfigs/{config_id}"
        ),
        create_time=None,
        update_time=None,
        last_login_time=None,
    )


@pytest.fixture
def app_runner():
    from typer.testing import CliRunner

    return CliRunner()
