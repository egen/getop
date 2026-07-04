# Agent instructions for getop

This repository is built and maintained with AI coding agents. If you are an
agent working here, follow these rules.

## Use the project subagents

Definitions live in `.claude/agents/`. Delegate explicitly ("Use the X
subagent to …") rather than doing everything in the main session:

| Subagent | When to use |
|---|---|
| `ge-api-researcher` | **Before** touching any API surface: verifies current google-cloud-discoveryengine method names, Cloud Logging field names/filters, and metric types against official docs so nothing is hallucinated. |
| `discoveryengine-lister` | Changes to `getop/commands/ls.py` (inventory listing). |
| `logging-inspector` | Changes to `getop/commands/logs.py` (log filters, tailing, normalization). |
| `monitoring-stats` | Changes to `getop/commands/stats.py` (metrics, quotas). |
| `readonly-auditor` | **After** any change that touches API calls: greps the tree for mutating calls and confirms the read-only guarantees still hold. |

The main session owns the shared contracts (`getop/auth.py`, `getop/render.py`,
`getop/duration.py`, `getop/main.py`, `getop/commands/doctor.py`) and the final
wiring; builders must not edit files outside their charter.

## Skills

Run `/ge-api-drift` (defined in `.claude/skills/ge-api-drift/`) whenever the
google-cloud-discoveryengine dependency is upgraded, before a release, or when
a command misbehaves against a live project — it reconciles every API surface
getop uses against the installed client and current docs, and highlights new
or deprecated calls.

## Hard constraints

- **Read-only.** getop must never call a mutating RPC (create/update/patch/
  delete/import/purge). Only `list_*`, `get_*`, `entries.list`,
  `list_time_series` / `list_metric_descriptors`, and GET-only REST via
  `Clients.rest_get`. `tests/test_readonly.py` enforces this — keep it passing.
- **Clients are constructed only in `getop/auth.py`** (ADC only, no key files).
- `getop logs user` must call `render.warn_banner()` before any other output.
- Every command supports `--json`; progress/warnings go to stderr, data to stdout.

## Workflow

`main` is protected: changes go through a feature branch and PR, and the
`test (3.11/3.12/3.13)` checks must pass. Run `uv run pytest -q` locally before
pushing. Verified live behaviour beats documentation — when docs and a live
log/metric disagree, trust the live shape and record it in a test.

### Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):
`<type>(<scope>): <imperative summary>` — e.g. `feat(logs): add gen_ai
content stream`, `fix(stats): humanize aggregate numbers`. Types: `feat`,
`fix`, `docs`, `test`, `refactor`, `perf`, `chore`, `ci`. Scope is the
command group or module (`ls`, `logs`, `stats`, `quota`, `doctor`, `auth`,
`render`, `agents`, `ci`). PR titles follow the same convention — they become
the squash-merge subject. Keep the body explaining *why*, and end agent
commits with the Co-Authored-By trailer.
