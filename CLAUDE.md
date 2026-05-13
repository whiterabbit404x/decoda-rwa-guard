# Decoda RWA Guard - Claude Code Instructions

## 1. Product identity

- This is Decoda RWA Guard, a B2B SaaS for monitoring tokenized real-world assets.
- The app must feel like a production SaaS product, not a mock demo.
- Customer-facing UI must distinguish live data, simulator data, unavailable data, and no data.

## 2. SaaS workflow

The canonical product workflow is:

Signup/login
→ Workspace
→ Onboarding
→ Asset registry
→ Monitoring target/system
→ Monitoring config
→ Runtime status
→ Telemetry
→ Detection
→ Alert
→ Incident
→ Response action
→ Evidence/export/audit

## 3. Truthfulness rules

- No data must not be shown as safe.
- No alert must not be shown as healthy.
- Simulator/fallback data must never be presented as customer evidence.
- Runtime status must be derived from canonical backend facts.
- Heartbeat, poll, and telemetry must be treated separately.
- Heartbeat proves the worker is alive.
- Poll proves the monitoring loop ran.
- Telemetry proves monitored data actually arrived.
- Do not claim live monitoring is healthy when reporting systems are zero.
- Do not claim telemetry is current when telemetry is missing or stale.
- Do not show simulator data as live customer evidence.

## 4. Architecture rules

- Use workspace-scoped data.
- Do not introduce unscoped cross-tenant queries.
- Do not add random mock data to production UI.
- Prefer existing backend endpoints and shared runtime summary builders.
- Add tests for any behavior change.
- Preserve the asset → target → monitoring config → telemetry → detection → alert → incident → action → export workflow.
- Keep customer-facing status labels truthful and fail-closed.

## 5. Implementation style

- Keep changes small.
- Avoid large rewrites.
- Preserve existing route names unless necessary.
- Run relevant tests before final response.
- End by summarizing changed files and remaining risks.
- If a session is audit-only, do not modify application code.
- If the task is unclear, make a minimal safe improvement and explain the limitation.

## 6. 403 / permission fallback

If a push, PR creation, or remote write fails with a 403, permission error, push denied, "Resource not accessible by integration", or "failed to push some refs":

1. Stop trying to push.
2. Do not retry.
3. Do not change authentication settings.
4. Do not delete or reset branches.
5. Keep all code changes in the working tree.
6. Run:

```bash
git status
git diff --stat
git diff > claude-session.patch