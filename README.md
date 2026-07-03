# geadm

[![Gemini Enterprise](https://img.shields.io/badge/Gemini%20Enterprise-Discovery%20Engine-4285F4?logo=googlecloud&logoColor=white)](https://cloud.google.com/gemini/enterprise)
[![PyPI](https://img.shields.io/pypi/v/geadm)](https://pypi.org/project/geadm/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)

**geadm** is a command-line companion for operating **Google Gemini Enterprise**.
It answers the questions that come up daily while running a deployment — What's
deployed? Are the connectors syncing? What are users asking, and what is Model
Armor flagging? How close are we to a quota ceiling? Is everything healthy? — in
one screen each, straight from your terminal.

```console
$ geadm info
                     Gemini Enterprise — acme-search-prod (global)

╭─────── Engines ───────╮ ╭───── Data stores ─────╮ ╭────── Connectors ──────╮
│           2           │ │           9           │ │      3/3 ACTIVE        │
╰───────────────────────╯ ╰───────────────────────╯ ╰────────────────────────╯
╭─────── Agents ────────╮ ╭─────── Licenses ──────╮
│          41           │ │    27/500 (5.4%)      │
╰───────────────────────╯ │    21/27 logged in    │
                          │  12 awaiting license  │
                          ╰───────────────────────╯

╭─────────────── Support Search ───────────────╮ ╭─────────────── Sandbox ──────────────╮
│ SEARCH · GENERIC · intranet · 2026-04-08     │ │ SEARCH · GENERIC · intranet · 2026-03-26│
│ Data stores (6)                              │ │ Data stores (3)                        │
│   • acme-sharepoint_1774543_file ← sharepoint│ │   • acme-sharepoint_1774543_file ← shar…│
│   • acme-sharepoint_1774543_page ← sharepoint│ │   • acme-onedrive_1775136_file ← onedrive│
│   • acme-onedrive_1775136_file ← onedrive    │ │   • acme-gdrive_1778773_file ← google_dr…│
│ Agents (37 — 3 enabled, 34 user defaults)    │ │ Agents (4 — 4 enabled, 0 user defaults)│
│   • Deep Research ENABLED                    │ │   • Security Reviewer ENABLED          │
│   • Contract Analyst PRIVATE                 │ │   • Deep Research ENABLED              │
│   • My Agent ×34 (user defaults, private)    │ │   • Idea Generation ENABLED            │
│ Features (16 on · 5 off)                     │ │ Features (14 on · 7 off)               │
│ ✓ agent-gallery model-selector notebook-lm   │ │ ✓ agent-gallery model-selector         │
│ ✗ agent-sharing-without-admin-approval canvas│ │ ✗ session-sharing onedrive-upload      │
╰────────── support-search_1775663018 ─────────╯ ╰──────── sandbox_1774543712 ──────────╯
```

Two engines side by side — spot the config drift at a glance: sandbox has
`session-sharing` and `onedrive-upload` disabled where prod allows them.

By design, the current release is strictly read-only — every command works with
viewer roles alone, so it can be handed to anyone on the team without
change-risk. It may grow administrative verbs (e.g. triggering connector syncs,
managing agents) in a future release.

Contributing with an AI coding agent? Read [AGENTS.md](AGENTS.md) — it maps the
project subagents, skills and hard constraints.

## Install

```sh
pipx install geadm
```

Also works with `uv tool install geadm` or `pip install geadm`. See
[Authentication](#authentication) for the one-time credential setup.

## Commands

### `geadm info` — project overview

A whole-project dashboard: summary tiles (engines, data stores, connector
health, agents, and license seats / activation / unmet demand) plus a card per
engine showing its data stores with their connector sources, its agents ("My
Agent" user defaults collapsed into a single ×N line), and its feature toggles.
Diffing two environments' cards is the fastest way to spot config drift. Output
is shown at the top of this page.

### `geadm ls` — inventory

```sh
geadm ls engines|datastores|connectors|agents|licenses
```

Lists resources across the collection hierarchy: engines and data stores under
`default_collection`, data connectors across *all* collections (each
connector-backed source lives in its own), agents per engine, and user licenses.

```console
$ geadm ls connectors
                               Data Connectors
┏━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┓
┃ Collection           ┃ Data Source ┃ State  ┃ Refresh Interval ┃ Entities ┃
┡━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━┩
│ acme-sharepoint_1774 │ sharepoint  │ ACTIVE │ 86400s           │ 5        │
│ acme-onedrive_1775   │ onedrive    │ ACTIVE │ 86400s           │ 1        │
└──────────────────────┴─────────────┴────────┴──────────────────┴──────────┘

$ geadm ls licenses
                                 User Licenses
┏━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┓
┃ User              ┃ State    ┃ Config      ┃ Last Login          ┃
┡━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━┩
│ alice@example.com │ ASSIGNED │ enterprise  │ 2026-07-03T12:47:25 │
│ bob@example.com   │ ASSIGNED │ enterprise  │ 2026-07-03T11:25:22 │
└───────────────────┴──────────┴─────────────┴─────────────────────┘
```

### `geadm logs` — view and tail logs

```sh
geadm logs connector [--datastore ID] [--severity ERROR] [--since 1h]
geadm logs user [email] [--since 24h] [--follow]
geadm logs ai [--since 24h] [--follow]
```

`connector` shows data-connector sync activity. `user` shows end-user activity —
prompts, assistant replies, searches, and Model Armor screening events — for one
user, or all users when the email is omitted. `ai` streams the raw
`gen_ai.user.message` / `gen_ai.choice` content logs (no identity field, so it
can't be scoped per user — use `logs user` for that). `--follow`/`-f` tails
either stream live. When a log is empty, geadm tells you whether logging simply
isn't enabled on the project or nothing matched your filter.

```console
$ geadm logs user --since 24h
╭──────────────────────────────── SENSITIVE ─────────────────────────────────╮
│ ⚠  Output may include end-user prompt/response content if prompt/response   │
│ logging is enabled on this project.                                         │
╰─────────────────────────────────────────────────────────────────────────────╯
                          User activity: all users (24h)
┏━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┓
┃ Time     ┃ Severity ┃ User            ┃ Message               ┃ Reply (truncated)   ┃
┡━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━┩
│ 16:57:06 │ INFO     │ alice@example.… │ StreamAssist: What's  │ Your travel policy  │
│          │          │                 │ our travel policy?    │ allows economy fa…  │
│ 12:49:04 │ WARNING  │ bob@example.com │ ModelArmorAudit:      │                     │
│          │          │                 │ Ignore all previous…  │                     │
└──────────┴──────────┴─────────────────┴───────────────────────┴─────────────────────┘
```

The `WARNING` row is Model Armor flagging a prompt-injection attempt. Tail it
live with `geadm logs user -f`, or pull full transcripts with `--json`.

### `geadm armor` — Model Armor violations

```sh
geadm armor [--since 24h] [--all]
```

Surfaces prompts and responses that Model Armor flagged, with the filters
that tripped and their confidence (jailbreak, RAI categories, CSAM,
malicious URIs). Violations only by default; `--all` includes clean
screenings. Carries no user identity — pair with `geadm logs user`.

Add `--policy` to print the configured Model Armor template(s) instead — the
filters that are enabled and at what confidence:

```console
$ geadm armor --policy
            Model Armor policy — ge-default-amer (us)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┓
┃ Filter                            ┃ Enforcement ┃ Confidence       ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━┩
│ Prompt injection & jailbreak      │ ENABLED     │ HIGH             │
│ Responsible AI: DANGEROUS         │ ENABLED     │ MEDIUM_AND_ABOVE │
│ Malicious URIs                    │ ENABLED     │                  │
└───────────────────────────────────┴─────────────┴──────────────────┘
```

```console
$ geadm armor --since 7d
                          Model Armor violations (7d)
┏━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━┓
┃ Time     ┃ Direction ┃ Match       ┃ Filters                   ┃ Content        ┃
┡━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━┩
│ 12:49:03 │ prompt    │ MATCH_FOUND │ pi_and_jailbreak(HIGH),   │ Ignore all     │
│          │           │             │ rai:dangerous(HIGH)       │ previous inst… │
└──────────┴───────────┴─────────────┴───────────────────────────┴────────────────┘
```

### `geadm stats` — metrics

```sh
geadm stats [--engine ID] [--since 24h]
```

Discovers the project's `discoveryengine.googleapis.com` metrics at runtime and
summarises query volume, latency and connector sync freshness over the window.

```console
$ geadm stats --since 24h
                 Discovery Engine metrics — acme-search-prod (global)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┓
┃ Metric type                       ┃ Category  ┃ Points ┃ Aggregate           ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━┩
│ …/serving/search_request_count    │ volume    │ 288    │ 1,204 (sum)         │
│ …/serving/search_latencies        │ latency   │ 288    │ 342 (p95)           │
│ …/dataconnector/synced_doc_count  │ connector │ 24     │ 48,102 (sum)        │
└───────────────────────────────────┴───────────┴────────┴─────────────────────┘
```

### `geadm quota` — quota usage

```sh
geadm quota [--since 24h]
```

Pairs each Discovery Engine quota's latest usage with its limit per location:
percent used (highlighted at ≥75% / ≥90%), byte quotas in human units, and
counts of quota-exceeded events over the window — the quickest way to spot the
next capacity ceiling before ingestion hits it.

```console
$ geadm quota
                Discovery Engine quotas — acme-search-prod (global)
┏━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━┓
┃ Quota                  ┃ Location ┃ Usage     ┃ Limit      ┃ Used ┃ Exceeded ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━┩
│ document_size_tier_st… │ global   │ 85.6 MiB  │ 1.0 GiB    │ 8.4% │          │
│ data_stores_regional   │ global   │ 8         │ 100        │ 8.0% │          │
│ documents_regional     │ global   │ 76        │ 10,000,000 │ 0.0% │          │
└────────────────────────┴──────────┴───────────┴────────────┴──────┴──────────┘
```

### `geadm doctor` — health check

```sh
geadm doctor [--since 24h]
```

Runs the whole suite concurrently and renders a live PASS/WARN/FAIL table across
inventory reachability, connector states and sync freshness, connector/API error
logs, and metric availability. Exits non-zero if any check fails, so it drops
straight into CI or cron.

```console
$ geadm doctor
                     geadm doctor — acme-search-prod (global)
┏━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Check                  ┃ Status ┃ Detail                                    ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ engines                │ OK     │ 1 engine(s): support-search               │
│ datastores             │ OK     │ 6 data store(s)                           │
│ data connectors        │ OK     │ 2 connector(s), all ACTIVE, synced <24h   │
│ agents                 │ OK     │ 37 agent(s) across 1 engine(s)            │
│ connector errors (24h) │ OK     │ no connector ERROR logs since 24h         │
│ API errors (24h)       │ OK     │ no discoveryengine API ERROR logs         │
│ monitoring metrics     │ OK     │ 369 metric type(s) discovered             │
└────────────────────────┴────────┴───────────────────────────────────────────┘
```

### `geadm version`

```console
$ geadm version
geadm 0.2.2 (v0.2.2, commit a1854e8)
```

Prints the release, tag and the git commit the package was built from.

### `geadm update`

```console
$ geadm update
Update available: 0.2.2 → 0.3.0
$ pipx upgrade geadm
```

Checks PyPI for a newer release and upgrades in place, detecting whether you
installed via pipx, uv or pip. Use `--check` to only report.

### Global options

Available on every command:

| Switch | Meaning |
|---|---|
| `--project` / `-p` | Target GCP project. Defaults to the ADC project; also reads `GOOGLE_CLOUD_PROJECT`. |
| `--location` / `-l` | Gemini Enterprise location (default `global`). Regional values route to `{location}-discoveryengine.googleapis.com`. |
| `--quota-project` | Project to bill API quota against when you lack `serviceusage.services.use` on the target; also reads `GOOGLE_CLOUD_QUOTA_PROJECT`. |
| `--json` | Machine-readable output on every command (newline-delimited JSON in `--follow` mode). |
| `--since` | Look-back window on time-based commands: `30m`, `1h`, `24h`, `7d`. |

Every table above is also available as JSON — pipe it straight into `jq`:

```sh
geadm quota --json | jq '.[] | select(.percent_used > 75)'
geadm ls licenses --json | jq '[.[] | select(.last_login_time == null)] | length'
```

## Authentication

Authenticate once with Application Default Credentials:

```sh
gcloud auth application-default login
```

geadm never reads or writes key files. Each command needs only a viewer role:

| Role | Used by |
|---|---|
| `roles/discoveryengine.viewer` | `geadm ls …`, `geadm info`, `geadm doctor` |
| `roles/logging.viewer` | `geadm logs …`, `geadm doctor` |
| `roles/monitoring.viewer` | `geadm stats`, `geadm quota`, `geadm doctor` |
| `roles/modelarmor.viewer` | `geadm armor --policy` |

User credentials (as opposed to service accounts) also need a quota project:
geadm uses the target project automatically, which requires
`serviceusage.services.use` there. If you don't have it, use `--quota-project`.

Enabling connector/observability *logging* on a project is a one-time setup
step requiring `roles/discoveryengine.agentspaceAdmin`; geadm only ever reads
what's there.

## License

MIT © Egen — see [LICENSE](LICENSE).
