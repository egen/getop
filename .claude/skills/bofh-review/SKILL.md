---
name: bofh-review
description: Merciless pre-publication audit of everything that becomes visible when the repo goes public or a release ships — secrets and identifiers in full git history, PR/issue bodies and comments, Actions logs, package artifact contents, dependency licenses, and public-project security posture. Verdict is NO COMEBACK or EXPOSED with required fixes. Run before flipping visibility and before any release while findings are cheap to fix.
---

# BOFH review — the "no comeback" gate

Assume every byte will be read by a competitor, a journalist, and opposing
counsel. Your job is to find what they would find, first. Report-only:
produce findings and a verdict; fixes happen as normal PRs afterwards.

Severity: **BLOCKER** (do not publish), **RISK** (publish invites trouble),
**SLOPPY** (won't sue you, will embarrass you).

## 1. Secrets — tree and full history

- Working tree: scan for key/token/credential patterns (`AKIA`, `AIza`,
  `ghp_`, `gho_`, `-----BEGIN`, `client_secret`, `password\s*=`, high-entropy
  strings) across all files including dotfiles.
- **Full pushed history**: `git log -p --all` piped through the same patterns
  — a secret deleted in a later commit is still published history. If
  `gitleaks` or `trufflehog` is installed, run it too (`gitleaks detect
  --source .`); don't install tools just for this.
- `.gitignore`d files never uploaded ≠ safe: check nothing ignored is
  referenced by committed code as if it will exist.

## 2. Identifiers — everywhere GitHub will render

Internal/client names (org names, GCP project IDs, personal emails, internal
hostnames) in:

- tree and full history (`git log -p --all | grep -inE '<patterns>'`)
- commit **messages** on all refs
- **PR and issue bodies AND comments** (`gh pr list --state all --json
  number,body,comments`, same for issues) — these render publicly and are
  editable after the fact
- release notes / CHANGELOG
- **Actions run logs** — private-era logs become readable on flip; sample the
  logs of every workflow (`gh run view <id> --log | grep -iE '<patterns>'`).
  Logs cannot be edited, only deleted (`gh api -X DELETE
  repos/{owner}/{repo}/actions/runs/{id}/logs`).

Build the pattern list from context (client names, `--project` values seen in
session history, personal emails) — do not rely on a canned list alone.

## 3. Package artifacts — what actually ships

- `uv build`, then list **both** sdist and wheel contents in full. The wheel
  should contain only the package; the sdist only package + build/docs
  essentials. Flag anything else (agent configs, CI files, scratch, tests
  with realistic data) — decide deliberately, don't ship by default.
- Grep the built artifacts for the identifier patterns from §2.

## 4. Legal and trademark

- Every runtime dependency's license must be MIT-compatible (`uv run python
  -c` over importlib.metadata, or check each dist's METADATA License field).
- Trademark posture: product names (Google, Gemini) used nominatively only —
  the description must not imply endorsement ("for", not "by"); no logos
  copied into the repo.
- LICENSE year/holder correct; README claims (e.g. "read-only") must be
  true — an inaccurate safety claim is a liability, verify against
  tests/auditor.

## 5. Public-project posture

- SECURITY.md with a private disclosure route (email, not public issues).
- Workflow hygiene: third-party actions pinned (tags minimum, SHAs better),
  workflow `permissions` minimal, no `pull_request_target` footguns,
  environment protecting the publish.
- Branch protection still on; repo description/topics presentable.

## 6. Verdict

A table: finding → severity → surface → required fix. Then one line:
**NO COMEBACK** (nothing above SLOPPY, and SLOPPYs listed) or **EXPOSED**
(any BLOCKER/RISK, publish blocked until fixed). Re-run after fixes; the
verdict must be earned, not negotiated.
