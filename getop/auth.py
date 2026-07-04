"""Client factory for getop.

This is the ONLY place API clients are constructed. Every client here is used
strictly read-only: list_*, get_*, entries.list (logging) and
list_time_series / query (monitoring). getop never calls a mutating RPC.

Auth is Application Default Credentials via google.auth.default(). No key files.
Required caller roles: roles/discoveryengine.viewer, roles/logging.viewer,
roles/monitoring.viewer.
"""

from __future__ import annotations

import threading
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
    quota_project: str | None = None
    _cache: dict[str, Any] = field(default_factory=dict, repr=False)
    # RLock: building a client re-enters _cached for the shared credentials.
    _lock: Any = field(default_factory=threading.RLock, repr=False)

    def _cached(self, key: str, build: Any) -> Any:
        """Thread-safe lazy construction (doctor runs checks concurrently)."""
        if key not in self._cache:
            with self._lock:
                if key not in self._cache:
                    self._cache[key] = build()
        return self._cache[key]

    @property
    def _credentials(self) -> Any:
        """ADC credentials with the target project as quota project.

        User ADC has no quota project by default and discoveryengine rejects
        such calls; billing quota against the inspected project matches what
        `gcloud auth application-default set-quota-project` would do.
        """
        def build() -> Any:
            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            if hasattr(credentials, "with_quota_project"):
                credentials = credentials.with_quota_project(
                    self.quota_project or self.project
                )
            return credentials

        return self._cached("credentials", build)

    # ---- Discovery Engine -------------------------------------------------
    def discoveryengine(self, client_cls: type) -> Any:
        """Build (once) a Discovery Engine service client of the given class,
        e.g. discoveryengine_v1.EngineServiceClient, with the regional
        api_endpoint set when location != "global".
        """
        key = f"de:{client_cls.__module__}.{client_cls.__qualname__}"

        def build() -> Any:
            endpoint = _regional_endpoint(self.location)
            options = ClientOptions(api_endpoint=endpoint) if endpoint else None
            return client_cls(credentials=self._credentials, client_options=options)

        return self._cached(key, build)

    # ---- Cloud Logging ----------------------------------------------------
    @property
    def logging(self) -> Any:
        """google.cloud.logging_v2.Client scoped to the project (entries.list only)."""
        def build() -> Any:
            from google.cloud import logging_v2

            return logging_v2.Client(project=self.project, credentials=self._credentials)

        return self._cached("logging", build)

    # ---- Cloud Monitoring ---------------------------------------------------
    @property
    def monitoring(self) -> Any:
        """monitoring_v3.MetricServiceClient (list_time_series / descriptors only)."""
        def build() -> Any:
            from google.cloud import monitoring_v3

            return monitoring_v3.MetricServiceClient(credentials=self._credentials)

        return self._cached("monitoring", build)

    # ---- REST fallback (GET only) -------------------------------------------
    def rest_get(
        self,
        path: str,
        params: dict[str, str] | None = None,
        host: str | None = None,
    ) -> dict:
        """HTTP GET against a Google Cloud REST API (Discovery Engine by
        default, regional-aware; pass `host` for other read surfaces such as
        logging.googleapis.com logs.list).

        For read surfaces the published Python clients don't expose
        (e.g. v1alpha dataConnector / assistants agents). GET only — this
        helper cannot issue mutating requests.

        `path` is the versioned resource path, e.g.
        "v1alpha/projects/p/locations/global/collections/default_collection/dataConnector".
        """
        def build() -> Any:
            from google.auth.transport.requests import AuthorizedSession

            return AuthorizedSession(self._credentials)

        session = self._cached("session", build)
        if host is None:
            host = _regional_endpoint(self.location) or "discoveryengine.googleapis.com"
        resp = session.get(
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


def get_clients(
    project: str | None,
    location: str = "global",
    quota_project: str | None = None,
) -> Clients:
    """Return the shared read-only client factory.

    If project is None, falls back to the ADC default project. quota_project
    lets user-credential callers bill API quota to a project they hold
    serviceusage.services.use on when they lack it on the target project.
    """
    resolved = project or _adc_project()
    if not resolved:
        raise SystemExit(
            "No project specified and ADC has no default project. "
            "Pass --project or run `gcloud auth application-default login`."
        )
    return Clients(
        project=resolved, location=location or "global", quota_project=quota_project
    )
