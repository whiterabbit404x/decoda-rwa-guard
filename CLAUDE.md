1. Product identity
Decoda RWA Guard is a B2B SaaS platform for monitoring tokenized real-world assets (RWAs).

The product must feel like a serious production SaaS product, not a mock demo dashboard.

Customer-facing UI must clearly distinguish:

live data

simulator data

unavailable data

no data

2. SaaS workflow
The product workflow should remain aligned with this production SaaS path:

Signup/login

Workspace

Onboarding

Asset registry

Monitoring target/system

Monitoring config

Runtime status

Telemetry

Detection

Alert

Incident

Response action

Evidence/export/audit

Do not skip core workflow steps by replacing them with static demo screens.

3. Truthfulness rules
No data must not be shown as safe.

No alert must not be shown as healthy.

Simulator or fallback data must never be presented as customer evidence.

Runtime status must be derived from canonical backend facts.

Heartbeat, poll, and telemetry must be treated separately:

heartbeat proves the worker/service is alive

poll proves the monitoring loop ran

telemetry proves monitored data actually arrived

Do not claim live monitoring is healthy when reporting systems are zero.

Do not claim telemetry is current when telemetry is missing or stale.

Do not show simulator data as live customer evidence.

4. Architecture rules
Use workspace-scoped data.

Do not introduce unscoped cross-tenant queries.

Do not add random mock data to production UI.

Prefer existing backend endpoints and shared runtime summary builders.

Use canonical runtime/status facts where available instead of inventing frontend-only status.

Add tests for any behavior change.

Preserve the asset -> target -> monitoring config -> telemetry -> detection -> alert -> incident -> action -> export workflow.

Keep customer-facing status labels truthful and fail-closed.

5. Implementation style
Keep changes small.

Avoid large rewrites.

Preserve existing route names unless a route change is clearly necessary.

Run relevant tests before final response.

If a session is audit-only, do not modify application code.

If the task is unclear, make a minimal safe improvement and explain the limitation.

End implementation responses by summarizing:

changed files

tests run

pass/fail result

remaining risks

6. 403 / permission fallback
If a push, PR creation, or remote write fails with a 403, permission error, push denied, Resource not accessible by integration, or failed to push some refs:

Stop trying to push.

Do not retry.

Do not change authentication settings.

Do not delete or reset branches.

Keep all code changes in the working tree.

Run these commands:

git status
git diff --stat
git diff > claude-session.patch

If there are new untracked files, print each new file path and full file content because git diff may not include untracked files.

Final response must include:

exact error message

changed files

tests run

pass/fail result

patch file path

full patch content if reasonable

manual commands for the user to run locally

Manual fallback commands:

git status
git add .
git commit -m "<short commit message>"
git push origin HEAD:<current-branch-name>
7. Session completion checklist
At the end of every implementation session, report:

changed files

tests run

pass/fail result

remaining risks

whether any demo/fallback/simulator data was touched

whether any workspace-scoped query was added or changed