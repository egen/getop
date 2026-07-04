# getop

[![Gemini Enterprise](https://img.shields.io/badge/Gemini%20Enterprise-Discovery%20Engine-4285F4?logo=googlecloud&logoColor=white)](https://cloud.google.com/gemini/enterprise)
[![PyPI](https://img.shields.io/pypi/v/getop?label=PyPI&color=006dad)](https://pypi.org/project/getop/)
[![CI](https://github.com/egen/getop/actions/workflows/test.yml/badge.svg)](https://github.com/egen/getop/actions/workflows/test.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/pypi/l/getop?color=green)](LICENSE)

**getop** is a command-line tool for Google Gemini Enterprise administrators.

getop allows you to:

- **Inventory everything deployed** — engines, data stores, connectors, agents, and license seats in one overview
- **Monitor connector health** — sync state, freshness, and errors across every collection
- **Investigate prompts and replies (if logging is enabled)** — per user or fleet-wide, live-tailable
- **See what Model Armor caught** — jailbreak, RAI, and malicious-URI hits, plus the active policy
- **Stay ahead of quota** — usage vs limits with one-click links to the Cloud console
- **Run a health check** — a parallel PASS/WARN/FAIL sweep with CI-friendly exit codes

Read-only by design — every command runs with viewer roles alone.

```console
$ getop info
             Gemini Enterprise — acme-search-prod (global)

╭──── Engines ─────╮  ╭── Data stores ───╮  ╭──── Connectors ────╮
│        2         │  │        9         │  │     2/3 ACTIVE     │
╰──────────────────╯  ╰──────────────────╯  ╰────────────────────╯
╭───── Agents ─────╮  ╭───── Licenses ─────╮
│        41        │  │   418/500 (84%)    │
╰──────────────────╯  ╰────────────────────╯

╭────────────── Support Search ──────────────╮  ╭───────────────── Sandbox ──────────────────╮
│ SEARCH · GENERIC · intranet · 2026-04-08   │  │ SEARCH · GENERIC · intranet · 2026-03-26   │
│ Data stores (4)                            │  │ Data stores (3)                            │
│   • sharepoint_1774_file  ← sharepoint     │  │   • sharepoint_1774_file  ← sharepoint     │
│   • sharepoint_1774_page  ← sharepoint     │  │   • onedrive_1775_file    ← onedrive       │
│   • onedrive_1775_file    ← onedrive       │  │   • gdrive_1778_file      ← google_drive   │
│   • jira_1778_issue  ← jira  PAUSED        │  │ Agents (4 — 4 enabled, 0 defaults)         │
│ Agents (41 — 4 enabled, 37 defaults)       │  │   • Security Reviewer   ENABLED            │
│   • Deep Research      ENABLED             │  │   • Deep Research       ENABLED            │
│   • Contract Analyst   ENABLED             │  │   • Idea Generation     ENABLED            │
│   • My Agent ×37 (user defaults)           │  │ Features (14 on · 7 off)                   │
│ Features (16 on · 5 off)                   │  │   ✓ agent-gallery model-selector           │
│   ✓ agent-gallery model-selector           │  │   ✗ session-sharing onedrive-upload        │
│   ✗ agent-sharing-without-approval         │  ╰──────────── sandbox_1774543712 ────────────╯
╰──────── support-search_1775663018 ─────────╯
```

**Want to contribute?** getop was built with an AI-agent scaffold that ships in
the repo and is yours to use too — see [Contributing](#contributing).

## Install

```sh
pipx install getop
```

Also works with `uv tool install getop` or `pip install getop`. See
[Authentication](#authentication) for the one-time credential setup.

## Commands

### `getop info` — project overview

Project-wide dashboard: summary tiles plus a card per engine — data stores with
their connector sources, agents, and feature toggles. Diffing two environments'
cards is the fastest way to spot config drift (shown at the top of this page).

### `getop ls` — inventory

```sh
getop ls engines|datastores|connectors|agents|licenses
```

Lists resources across the collection hierarchy: engines and data stores under
`default_collection`, data connectors across *all* collections (each
connector-backed source lives in its own), agents per engine, and user licenses.

```console
$ getop ls connectors
                               Data Connectors
┏━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┓
┃ Collection           ┃ Data Source ┃ State  ┃ Refresh Interval ┃ Entities ┃
┡━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━┩
│ acme-sharepoint_1774 │ sharepoint  │ ACTIVE │ 86400s           │ 5        │
│ acme-onedrive_1775   │ onedrive    │ ACTIVE │ 86400s           │ 1        │
└──────────────────────┴─────────────┴────────┴──────────────────┴──────────┘

$ getop ls licenses
                                 User Licenses
┏━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┓
┃ User              ┃ State    ┃ Config      ┃ Last Login          ┃
┡━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━┩
│ alice@example.com │ ASSIGNED │ enterprise  │ 2026-07-03T12:47:25 │
│ bob@example.com   │ ASSIGNED │ enterprise  │ 2026-07-03T11:25:22 │
└───────────────────┴──────────┴─────────────┴─────────────────────┘
```

### `getop logs` — view and tail logs

```sh
getop logs connector [--datastore ID] [--severity ERROR] [--since 1h]
getop logs user [email] [--since 24h] [--follow]
getop logs ai [--since 24h] [--follow]
```

`connector` shows sync activity. `user` shows end-user activity — prompts,
replies, searches, and Model Armor events — for one user or all. `ai` streams
the raw `gen_ai` content logs (no identity field; use `user` to scope by
principal). `--follow`/`-f` tails any stream; an empty result says whether
logging is off or just unmatched.

```console
$ getop logs user --since 24h
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
live with `getop logs user -f`, or pull full transcripts with `--json`.

### `getop armor` — Model Armor violations

```sh
getop armor [--since 24h] [--all]
```

Surfaces prompts and responses that Model Armor flagged, with the filters
that tripped and their confidence (jailbreak, RAI categories, CSAM,
malicious URIs). Violations only by default; `--all` includes clean
screenings. Carries no user identity — pair with `getop logs user`.

Add `--summary` for a per-filter rollup — hit counts, last seen, and an example
input per category — instead of the event-by-event list:

```console
$ getop armor --summary --since 7d
              Model Armor hits by filter (7d)
┏━━━━━━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┓
┃ Filter           ┃ Hits ┃ Last seen           ┃ Example input       ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━┩
│ pi_and_jailbreak │ 3    │ 2026-07-03T12:49:03 │ Ignore all previou… │
│ malicious_uris   │ 2    │ 2026-07-03T09:14:00 │ http://testsafebro… │
└──────────────────┴──────┴─────────────────────┴─────────────────────┘
```

Add `--policy` to print the configured Model Armor template(s) instead — the
filters that are enabled and at what confidence:

```console
$ getop armor --policy
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
$ getop armor --since 7d
                          Model Armor violations (7d)
┏━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━┓
┃ Time     ┃ Direction ┃ Match       ┃ Filters                   ┃ Content        ┃
┡━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━┩
│ 12:49:03 │ prompt    │ MATCH_FOUND │ pi_and_jailbreak(HIGH),   │ Ignore all     │
│          │           │             │ rai:dangerous(HIGH)       │ previous inst… │
└──────────┴───────────┴─────────────┴───────────────────────────┴────────────────┘
```

### `getop stats` — metrics

```sh
getop stats [--engine ID] [--since 24h]
```

Discovers the project's `discoveryengine.googleapis.com` metrics at runtime and
summarises query volume, latency and connector sync freshness over the window.

```console
$ getop stats --since 24h
                 Discovery Engine metrics — acme-search-prod (global)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┓
┃ Metric type                       ┃ Category  ┃ Points ┃ Aggregate           ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━┩
│ …/serving/search_request_count    │ volume    │ 288    │ 1,204 (sum)         │
│ …/serving/search_latencies        │ latency   │ 288    │ 342 (p95)           │
│ …/dataconnector/synced_doc_count  │ connector │ 24     │ 48,102 (sum)        │
└───────────────────────────────────┴───────────┴────────┴─────────────────────┘
```

### `getop quota` — quota usage

```sh
getop quota [--since 24h]
```

Pairs each Discovery Engine quota's latest usage with its limit per location:
percent used (highlighted at ≥75% / ≥90%), byte quotas in human units, and
counts of quota-exceeded events over the window — the quickest way to spot the
next capacity ceiling before ingestion hits it. On terminals that support
hyperlinks, each quota name links to the project's Cloud console quotas page.

```console
$ getop quota
                Discovery Engine quotas — acme-search-prod (global)
┏━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━┓
┃ Quota                  ┃ Location ┃ Usage     ┃ Limit      ┃ Used ┃ Exceeded ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━┩
│ document_size_tier_st… │ global   │ 85.6 MiB  │ 1.0 GiB    │ 8.4% │          │
│ data_stores_regional   │ global   │ 8         │ 100        │ 8.0% │          │
│ documents_regional     │ global   │ 76        │ 10,000,000 │ 0.0% │          │
└────────────────────────┴──────────┴───────────┴────────────┴──────┴──────────┘
```

### `getop doctor` — health check

```sh
getop doctor [--since 24h]
```

Runs the whole suite concurrently and renders a live PASS/WARN/FAIL table across
inventory reachability, connector states and sync freshness, connector/API error
logs, and metric availability. Exits non-zero if any check fails, so it drops
straight into CI or cron.

```console
$ getop doctor
                     getop doctor — acme-search-prod (global)
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

### `getop version`

```console
$ getop version
getop 0.2.2 (v0.2.2, commit a1854e8)
```

Prints the release, tag and the git commit the package was built from.

### `getop update`

```console
$ getop update
Update available: 0.2.2 → 0.3.0
$ pipx upgrade getop
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
getop quota --json | jq '.[] | select(.percent_used > 75)'
getop ls licenses --json | jq '[.[] | select(.last_login_time == null)] | length'
```

## Authentication

Authenticate once with Application Default Credentials:

```sh
gcloud auth application-default login
```

getop never reads or writes key files. Each command needs only a viewer role:

| Role | Used by |
|---|---|
| `roles/discoveryengine.viewer` | `getop ls …`, `getop info`, `getop doctor` |
| `roles/logging.viewer` | `getop logs …`, `getop doctor` |
| `roles/monitoring.viewer` | `getop stats`, `getop quota`, `getop doctor` |
| `roles/modelarmor.viewer` | `getop armor --policy` |

User credentials (as opposed to service accounts) also need a quota project:
getop uses the target project automatically, which requires
`serviceusage.services.use` there. If you don't have it, use `--quota-project`.

Enabling connector/observability *logging* on a project is a one-time setup
step requiring `roles/discoveryengine.agentspaceAdmin`; getop only ever reads
what's there.

## Contributing

Honest disclosure: getop was built almost entirely by an AI coding agent
(Anthropic's Claude, via Claude Code), directed by a human maintainer, using a
small scaffold that lives in this repo — and that scaffold is here for you to
use too.

**How it was built.** The work was split across specialised subagents in
[`.claude/agents/`](.claude/agents/) rather than one big prompt: a
`ge-api-researcher` that verifies Google Cloud API surfaces *before* any code is
written (so method names and log fields aren't hallucinated), per-domain
builders (`discoveryengine-lister`, `logging-inspector`, `monitoring-stats`)
that each own one command module, and a `readonly-auditor` that proves the tool
never calls a mutating API. Two skills in [`.claude/skills/`](.claude/skills/)
support ongoing work: `ge-api-drift` reconciles getop's API usage against the
installed client and current docs, and `bofh-review` is a pre-release
secret/leak/safety audit. Every command was live-tested against real Gemini
Enterprise deployments, and the read-only guarantee is enforced by a
source-scanning test ([`tests/test_readonly.py`](tests/test_readonly.py)) that
fails any mutating RPC, non-GET HTTP, or client built outside `getop/auth.py`.

**How to contribute.** [`AGENTS.md`](AGENTS.md) is the entry point — it maps the
subagents, skills, hard constraints, and workflow. By hand or with an agent:

```sh
uv sync && uv run pytest          # run the suite
```

- `main` is protected; changes go via a branch and PR with the tests passing.
- Follow [Conventional Commits](https://www.conventionalcommits.org/) — the PR
  title becomes the squash-merge subject and drives the release version.
- Keep it read-only; `tests/test_readonly.py` is the guardrail.
- Using an AI agent? Point it at `AGENTS.md` and let it drive the same subagents
  and skills that built getop in the first place.

## License

MIT © Egen — see [LICENSE](LICENSE).
