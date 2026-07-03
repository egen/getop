# geadm

[![Gemini Enterprise](https://img.shields.io/badge/Gemini%20Enterprise-Discovery%20Engine-4285F4?logo=googlecloud&logoColor=white)](https://cloud.google.com/gemini/enterprise)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)

**geadm** is a command-line companion for operating **Google Gemini Enterprise**.
It gives platform teams fast answers to the questions that come up daily while
running a Gemini Enterprise deployment: What's deployed? Are the connectors
syncing? What are users asking, and what is Model Armor flagging? How close are
we to a quota ceiling? Is everything healthy?

It inventories engines, data stores, connectors and agents; inspects and tails
the platform's Cloud Logging streams; summarises Cloud Monitoring metrics and
quota utilisation; and runs a one-shot health check across all of it.

By design, the current release is strictly read-only — every command works with
viewer roles alone, so it can be handed to anyone on the team without change-risk.
It may grow administrative verbs (e.g. triggering connector syncs, managing
agents) in a future release.

Contributing with an AI coding agent? Read [AGENTS.md](AGENTS.md) — it maps
the project subagents, skills and hard constraints.

## Install

```sh
uv tool install geadm      # or: pipx install geadm / pip install geadm
```

From a checkout:

```sh
uv tool install .
# or for development:
uv sync && uv run geadm --help
```

## Authentication & roles

geadm uses Application Default Credentials (`gcloud auth application-default login`);
it never reads or writes key files.

| Role | Used by |
|---|---|
| `roles/discoveryengine.viewer` | `geadm ls …`, `geadm doctor` |
| `roles/logging.viewer` | `geadm logs …`, `geadm doctor` |
| `roles/monitoring.viewer` | `geadm stats`, `geadm quota`, `geadm doctor` |

User credentials (as opposed to service accounts) also need a quota project:
geadm uses the target project automatically, which requires
`serviceusage.services.use` there. If you don't have it, pass
`--quota-project <other-project>` (or set `GOOGLE_CLOUD_QUOTA_PROJECT`) to bill
a project you can use.

Enabling connector/observability *logging* on a project is a one-time setup
step requiring `roles/discoveryengine.agentspaceAdmin`; geadm only ever reads
what's there.

## Commands

Global options: `--project` (defaults to the ADC project), `--location`
(default `global`; regional locations are routed to
`{location}-discoveryengine.googleapis.com`). Every command supports `--json`
for machine-readable output, and time-windowed commands take `--since`
(`30m`, `1h`, `24h`, `7d`).

### Overview — `geadm info`

```sh
geadm info
```

Project-wide dashboard: summary tiles (engines, data stores, connector
health, agents, license seats/activation/unmet demand) plus a card per
engine showing its data stores with their connector sources and its agents
("My Agent" user defaults are grouped into a single ×N line).

### Inventory — `geadm ls`

```sh
geadm ls engines|datastores|connectors|agents|licenses
```

Walks the collection hierarchy: engines and data stores under
`default_collection`, data connectors across *all* collections (each
connector-backed source lives in its own), agents per engine, and user
licenses in the project's `default_user_store`.

### Logs — `geadm logs`

```sh
geadm logs connector [--datastore ID] [--severity ERROR] [--since 1h]
geadm logs user [email] [--since 24h] [--follow]
geadm logs ai [--since 24h] [--follow]
```

`logs connector` shows data-connector sync activity. `logs user` shows
end-user Gemini Enterprise activity — prompts, assistant replies, searches and
Model Armor screening events — for one user, or all users when the email is
omitted. `logs ai` streams the raw `gen_ai.user.message`/`gen_ai.choice`
content logs (prompt and reply text with no identity field, so it cannot be
scoped per user — use `logs user` for that). `--follow`/`-f` tails either
stream live (newline-delimited JSON with `--json`). When a log turns out to be
empty, geadm tells you whether logging simply isn't enabled on the project or
nothing matched your filter.

> ⚠ **Sensitive output**: `geadm logs user` and `geadm logs ai` can surface
> end-user prompt and response content when prompt/response logging is
> enabled on the project, and print a warning banner before any output.

### Metrics — `geadm stats`

```sh
geadm stats [--engine ID] [--since 24h]
```

Discovers the project's `discoveryengine.googleapis.com` metrics at runtime and
summarises query volume, latency and connector sync freshness over the window.

### Quotas — `geadm quota`

```sh
geadm quota [--since 24h]
```

Pairs each Discovery Engine quota's latest usage with its limit per location:
percent used (highlighted at ≥75% / ≥90%), byte quotas in human units, and
counts of quota-exceeded events over the window — the quickest way to spot the
next capacity ceiling before ingestion hits it.

### Health check — `geadm doctor`

```sh
geadm doctor [--since 24h]
```

Runs the whole suite concurrently — inventory reachability, connector states
and sync freshness, connector/API error logs, metric availability — and renders
a live PASS/WARN/FAIL table. Exits non-zero if any check fails, so it drops
straight into CI or cron.
