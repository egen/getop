# geadm

Read-only troubleshooting / debug / stats CLI for **Google Gemini Enterprise**
(the Agentspace / Discovery Engine product, service `discoveryengine.googleapis.com` —
not Gemini Code Assist).

`geadm` never mutates anything: it only calls `list_*` / `get_*` methods on
Discovery Engine, `entries.list` on Cloud Logging, and
`list_time_series` / `list_metric_descriptors` on Cloud Monitoring.

## Install

```sh
uv tool install .          # from a checkout
# or for development:
uv sync && uv run geadm --help
```

## Auth & required roles

Authentication is Application Default Credentials (`gcloud auth application-default login`).
No key files are ever read or written.

The caller needs only viewer roles:

| Role | Used by |
|---|---|
| `roles/discoveryengine.viewer` | `geadm ls …`, `geadm doctor` |
| `roles/logging.viewer` | `geadm logs …`, `geadm doctor` |
| `roles/monitoring.viewer` | `geadm stats`, `geadm doctor` |

Note: *enabling* connector/observability logging on a project requires
`roles/discoveryengine.agentspaceAdmin` — that is a one-time project setup step,
not something `geadm` does or needs.

When authenticating as a user (not a service account), the Discovery Engine API
requires a quota project. `geadm` sets the target project as the quota project
automatically, which additionally requires `roles/serviceusage.serviceUsageConsumer`
(or any role containing `serviceusage.services.use`) on that project.

## Commands

```sh
geadm ls engines|datastores|connectors|agents   # inventory the default_collection hierarchy
geadm logs connector [--datastore ID] [--severity ERROR] [--since 1h]
geadm logs user <email> [--since 24h]           # ⚠ may surface end-user prompt/response content
geadm stats [--engine ID] [--since 24h]         # query volume, latency, connector sync freshness
geadm doctor                                    # composite read-only health check
```

Global options: `--project` (defaults to the ADC project), `--location`
(default `global`; regional locations use the
`{location}-discoveryengine.googleapis.com` endpoint). Every command supports
`--json` for machine output and log/stats commands take `--since` (`1h`, `24h`, `7d`).

### Sensitive output

`geadm logs user <email>` can surface end-user prompt/response content and
prints a warning banner before any output. Results depend on prompt/response
logging being enabled on the project.
