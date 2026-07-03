"""Client factory for geadm.

This is the ONLY place API clients are constructed. Every client here is used
strictly read-only: list_*, get_*, entries.list (logging) and
list_time_series / query (monitoring). geadm never calls a mutating RPC.

Auth is Application Default Credentials via google.auth.default(). No key files.
Required caller roles: roles/discoveryengine.viewer, roles/logging.viewer,
roles/monitoring.viewer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import google.auth
from google.api_core.client_options import ClientOptions


def _regional_endpoint(location: str) -> str | None:
    """Discovery Engine regional endpoint, or None for the global default."""
    if location and location != "global":
        return f"{location}-discoveryengine.googleapis.com"
    return None


@dataclass
class Clients:
    """Lazily constructs read-only clients wired to the right regional endpoint.

    Builder modules must obtain every client through this object (via
    get_clients()) and must never instantiate google-cloud clients directly.
    """

    project: str
    location: str = "global"
    _cache: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def _credentials(self) -> Any:
        """ADC credentials with the target project as quota project.

        User ADC has no quota project by default and discoveryengine rejects
        such calls; billing quota against the inspected project matches what
        `gcloud auth application-default set-quota-project` would do.
        """
        if "credentials" not in self._cache:
            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            if hasattr(credentials, "with_quota_project"):
                credentials = credentials.with_quota_project(self.project)
            self._cache["credentials"] = credentials
        return self._cache["credentials"]

    # ---- Discovery Engine -------------------------------------------------
    def discoveryengine(self, client_cls: type) -> Any:
        """Build (once) a Discovery Engine service client of the given class,
        e.g. discoveryengine_v1.EngineServiceClient, with the regional
        api_endpoint set when location != "global".
        """
        key = f"de:{client_cls.__module__}.{client_cls.__qualname__}"
        if key not in self._cache:
            endpoint = _regional_endpoint(self.location)
            options = ClientOptions(api_endpoint=endpoint) if endpoint else None
            self._cache[key] = client_cls(
                credentials=self._credentials, client_options=options
            )
        return self._cache[key]

    # ---- Cloud Logging ----------------------------------------------------
    @property
    def logging(self) -> Any:
        """google.cloud.logging_v2.Client scoped to the project (entries.list only)."""
        if "logging" not in self._cache:
            from google.cloud import logging_v2

            self._cache["logging"] = logging_v2.Client(
                project=self.project, credentials=self._credentials
            )
        return self._cache["logging"]

    # ---- Cloud Monitoring ---------------------------------------------------
    @property
    def monitoring(self) -> Any:
        """monitoring_v3.MetricServiceClient (list_time_series / descriptors only)."""
        if "monitoring" not in self._cache:
            from google.cloud import monitoring_v3

            self._cache["monitoring"] = monitoring_v3.MetricServiceClient(
                credentials=self._credentials
            )
        return self._cache["monitoring"]

    # ---- REST fallback (GET only) -------------------------------------------
    def rest_get(self, path: str, params: dict[str, str] | None = None) -> dict:
        """HTTP GET against the Discovery Engine REST API (regional-aware).

        For read surfaces the published Python clients don't expose
        (e.g. v1alpha dataConnector / assistants agents). GET only — this
        helper cannot issue mutating requests.

        `path` is the versioned resource path, e.g.
        "v1alpha/projects/p/locations/global/collections/default_collection/dataConnector".
        """
        if "session" not in self._cache:
            from google.auth.transport.requests import AuthorizedSession

            self._cache["session"] = AuthorizedSession(self._credentials)
        host = _regional_endpoint(self.location) or "discoveryengine.googleapis.com"
        resp = self._cache["session"].get(
            f"https://{host}/{path.lstrip('/')}", params=params or {}, timeout=60
        )
        resp.raise_for_status()
        return resp.json()

    # ---- Resource-name helpers ---------------------------------------------
    @property
    def collection_path(self) -> str:
        return (
            f"projects/{self.project}/locations/{self.location}"
            f"/collections/default_collection"
        )

    @property
    def monitoring_project_path(self) -> str:
        return f"projects/{self.project}"


@lru_cache(maxsize=None)
def _adc_project() -> str | None:
    _, project = google.auth.default()
    return project


def get_clients(project: str | None, location: str = "global") -> Clients:
    """Return the shared read-only client factory.

    If project is None, falls back to the ADC default project.
    """
    resolved = project or _adc_project()
    if not resolved:
        raise SystemExit(
            "No project specified and ADC has no default project. "
            "Pass --project or run `gcloud auth application-default login`."
        )
    return Clients(project=resolved, location=location or "global")
