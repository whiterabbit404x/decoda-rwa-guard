# CLAUDE.md — Decoda RWA Guard

## 1. Product identity

- Decoda RWA Guard is a B2B SaaS platform for monitoring tokenized real-world assets (RWAs).
- The product must feel like a serious production SaaS product, not a mock demo dashboard.
- Customer-facing UI must clearly distinguish:
  - live data
  - simulator data
  - unavailable data
  - no data

## 2. SaaS workflow

The product workflow should remain aligned with this production SaaS path:

1. Signup/login
2. Workspace
3. Onboarding
4. Asset registry
5. Monitoring target/system
6. Monitoring config
7. Runtime status
8. Telemetry
9. Detection
10. Alert
11. Incident
12. Response action
13. Evidence/export/audit

Do not skip core workflow steps by replacing them with static demo screens.

## 3. Truthfulness rules

- No data must not be shown as safe.
- No alert must not be shown as healthy.
- Simulator or fallback data must never be presented as customer evidence.
- Runtime status must be derived from canonical backend facts.
- Heartbeat, poll, and telemetry must be treated separately:
  - heartbeat proves the worker/service is alive
  - poll proves the monitoring loop ran
  - telemetry proves monitored data actually arrived

## 4. Architecture rules

- Use workspace-scoped data.
- Do not introduce unscoped cross-tenant queries.
- Do not add random mock data to production UI.
- Prefer existing backend endpoints and shared runtime summary builders.
- Use canonical runtime/status facts where available instead of inventing frontend-only status.
- Add tests for any behavior change.

## 5. Implementation style

- Keep changes small.
- Avoid large rewrites.
- Preserve existing route names unless a route change is clearly necessary.
- Run relevant tests before final response.
- End implementation responses by summarizing:
  - changed files
  - tests run
  - pass/fail result
  - remaining risks

## 6. 403 / permission fallback

If a push, PR creation, or remote write fails with a 403, permission error, push denied, Resource not accessible by integration, or failed to push some refs:

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