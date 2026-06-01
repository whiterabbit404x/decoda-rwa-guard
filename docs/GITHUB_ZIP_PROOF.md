# GitHub ZIP Proof

## The Problem

When you open a GitHub repository and click **Code → Download ZIP**, you get the
source tree committed to that branch. You do **not** get:

- GitHub Actions workflow run results
- Actions artifacts (uploaded via `actions/upload-artifact`)
- Any CI status badges or check summaries

Actions artifacts are stored in a separate GitHub storage layer. They expire after
30–90 days and are never part of the repository tree. A reviewer who only has the
downloaded ZIP cannot independently verify that CI passed.

## The Solution

`.github/workflows/save-proof-to-repo.yml` is a manual (`workflow_dispatch`)
workflow that:

1. Runs `scripts/proof/write_github_zip_proof.py` to generate two proof files.
2. Commits them directly into the `main` branch:
   - `artifacts/github-proof/latest/summary.json`
   - `artifacts/github-proof/latest/summary.md`
3. Uses commit message `Save latest GitHub ZIP proof [skip ci]` to avoid
   triggering another CI run.

After the workflow runs, anyone who downloads the ZIP via **Code → Download ZIP**
will find `artifacts/github-proof/latest/` in the archive.

## What the Proof Files Contain

### `summary.json`

```json
{
  "schema_version": 1,
  "generated_at": "<ISO-8601 timestamp>",
  "github_actions_visible_green": true,
  "repository": "<owner/repo>",
  "branch": "main",
  "commit": "<full SHA>",
  "run_id": "<GitHub Actions run ID>",
  "run_url": "<link to the Actions run>",
  "zip_includes_this_proof": true,
  "sell_now_proof": { ... } // or null if sell-now-proof artifact is absent
}
```

Key fields:

| Field | Meaning |
|---|---|
| `github_actions_visible_green` | The workflow ran successfully on this commit |
| `zip_includes_this_proof` | Always `true` — confirms this file reached the ZIP |
| `run_id` | GitHub Actions run ID; use it to look up the run online |
| `run_url` | Direct link to the Actions run for cross-referencing |

### `summary.md`

A human-readable table version of the same information, suitable for quick
inspection without a JSON viewer.

### Sell-now Proof Fields (when available)

If `artifacts/sell-now-proof/latest/summary.json` exists in the repository, the
following fields are copied verbatim into `sell_now_proof`:

| Field | Description |
|---|---|
| `sell_now_managed_ready` | Managed sell-now readiness flag |
| `broad_paid_saas_ready` | Whether broad paid SaaS launch is ready |
| `safe_to_sell_broadly_today` | Whether it is safe to sell broadly today |
| `provider_ready` | Whether the live provider is configured |
| `live_evidence_ready` | Whether live telemetry evidence is available |
| `evidence_source` | Where the evidence came from |
| `billing_ready` | Whether billing provider is configured |
| `email_ready` | Whether email provider is configured |

**Important:** These values are read directly from the artifact file. The script
never sets `broad_paid_saas_ready=true` or `live_evidence_ready=true` unless the
source file says so. If `sell-now-proof` is absent, `sell_now_proof` is `null`.

## How to Run

1. Go to the **Actions** tab on GitHub.
2. Select **Save GitHub ZIP Proof** from the workflow list.
3. Click **Run workflow** → **Run workflow**.
4. Wait for the run to complete (≈1 minute).
5. The commit `Save latest GitHub ZIP proof [skip ci]` will appear on `main`.

## How a Reviewer Inspects the ZIP

1. On GitHub, click **Code → Download ZIP** (or `git archive`).
2. Unzip the archive.
3. Open `artifacts/github-proof/latest/summary.json`.
4. Verify:
   - `github_actions_visible_green` is `true`.
   - `zip_includes_this_proof` is `true`.
   - `commit` matches the commit you expected.
   - `run_url` links to a real, completed Actions run.
5. Cross-reference `run_id` in the GitHub Actions UI or API.

## What This Does Not Prove

- That the full CI suite passed (use `artifacts/release-proof/` for that).
- That staging or production is healthy (use `artifacts/staging-proof/`).
- That billing/email/provider are configured (use `artifacts/launch-proof/`).

This file proves only that the workflow ran on the stated commit and that the
proof reached the repository tree.

## Script Location

```
scripts/proof/write_github_zip_proof.py
```

The script is deterministic: given the same GitHub environment variables and the
same `sell-now-proof` source file, it produces identical output. It reads no
secrets and writes no secrets.
