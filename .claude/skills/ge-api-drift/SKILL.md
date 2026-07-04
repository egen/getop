---
name: ge-api-drift
description: Reconcile every Gemini Enterprise / Discovery Engine API surface getop uses against the installed google-cloud-discoveryengine client and current Google Cloud docs, highlighting new or deprecated calls. Use after upgrading the client dependency, before a release, or when a command misbehaves against a live project.
---

# GE API drift check

Verify that the API surfaces getop depends on are still consistent with the
Discovery Engine product, and report anything new or deprecated. Do not change
behaviour in this skill — produce a report; fixes happen in a follow-up PR via
the owning subagent.

## Step 1 — inventory what getop uses

Build the "used surface" list from the code (don't work from memory):

- **Python client calls**: `grep -n "discoveryengine\|list_\|get_" getop/commands/*.py getop/auth.py` — collect every client class passed to `Clients.discoveryengine()` and every method invoked on it, plus `logging.list_entries`, `monitoring.list_time_series`, `list_metric_descriptors`.
- **REST paths**: every `rest_get(...)` call — note the API version segment (`v1alpha/...`) and resource shape (collections list, `dataConnector` singleton, `assistants/default_assistant/agents`, logging `v2/projects/{p}/logs`).
- **Log names & filter fields**: from `getop/commands/logs.py` — `connector_activity`, `gemini_enterprise_user_activity`, `jsonPayload.userIamPrincipal`, `jsonPayload.LogMetadata.name` / `logMetadata`, `serviceTextReply`, `request.query.text|parts|userPromptData`.
- **Metric filters**: from `getop/commands/stats.py` — the `starts_with("discoveryengine.googleapis.com/")` discovery filter and the `quota/<name>/{usage,limit,exceeded}` pattern.

## Step 2 — check against the installed client

Introspect the environment, not the docs, for the Python surface:

```sh
uv run python - <<'EOF'
import inspect
from google.cloud import discoveryengine_v1, discoveryengine_v1alpha
for mod in (discoveryengine_v1, discoveryengine_v1alpha):
    clients = [n for n in dir(mod) if n.endswith("ServiceClient")]
    print(mod.__name__, "->", clients)
EOF
```

- Confirm every used class/method still exists and emits no `DeprecationWarning` on import/call signature inspection.
- Flag **new** service clients relevant to getop's scope (e.g. a real
  `DataConnectorServiceClient` or agent-listing client appearing would let the
  v1alpha REST fallbacks in `ls.py` be replaced).
- Note the installed package version (`uv run python -c "import google.cloud.discoveryengine as d; print(d.__version__)"`) and the latest on PyPI.

## Step 3 — verify REST/logging/metric surfaces against docs

Use the `ge-api-researcher` subagent ("Use the ge-api-researcher subagent to
…") to confirm, from official Google Cloud documentation:

- the v1alpha REST resources getop GETs still exist at those paths, and whether
  any have been promoted to v1/v1beta (promotion = update the path);
- the Cloud Logging log names and payload fields (especially
  `jsonPayload.userIamPrincipal` and `serviceTextReply`) are still documented/
  current;
- any newly documented `discoveryengine.googleapis.com` metrics or quota
  metrics worth surfacing in `stats`/`quota`.

If docs and verified live behaviour (see memory / tests) disagree, trust the
live shape but flag the discrepancy.

## Step 4 — report

Produce a drift report with three sections:

1. **Consistent** — used surfaces confirmed current (one line each).
2. **Deprecated / changed** — anything removed, renamed, promoted to a newer
   API version, or emitting deprecation warnings; include the getop file:line
   that uses it and the recommended replacement.
3. **New / unused opportunities** — newly available clients, methods, log
   fields or metrics getop could adopt.

End with a verdict: `NO DRIFT`, or `DRIFT FOUND` plus which subagent should
implement each fix. Read-only throughout — this skill never edits getop code.
