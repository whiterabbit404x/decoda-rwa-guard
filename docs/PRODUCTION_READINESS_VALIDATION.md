# Production Readiness Validation

Internal/admin readiness is exposed at `GET /admin/readiness` (authenticated workspace admin scope).

## Categories

- **Platform**: database, auth/session config, required env presence (redacted), redis/email/billing config-or-disabled, app/api URL config.
- **Runtime**: heartbeat, latest poll, latest telemetry, reporting systems count, protected assets count, enabled monitoring config count, target coverage, provider health, freshness, confidence, contradiction flags.
- **Workflow**: detection/alert/incident/response-action counts, latest timestamps for each, linkage quality.
- **Evidence & Export**: evidence source status, export capability status, latest export job status, audit log availability, proof bundle capability (if available).
- **Integrations**: slack/webhook/delivery log statuses, API key support.
- **Security**: readiness access control, secret redaction, admin workspace scoping.

## Statuses
- `pass`: requirement satisfied.
- `warn`: non-blocking risk or intentionally disabled component.
- `fail`: blocking issue.
- `unavailable`: no trustworthy signal.

## Launch gates

### ready_for_pilot
Blocks when any of the following are true:
- DB unreachable.
- Auth/session missing.
- Workspace not evaluated.
- Workspace-scoped `protected_assets_count == 0`.
- Telemetry missing (heartbeat alone does not pass).
- Contradiction flags present.
- Evidence/export health not truthfully known/live.

`reporting_systems_count == 0` emits setup-required warning and must not appear healthy.

### ready_for_paid_public_launch
Requires `ready_for_pilot` plus:
- Billing configured unless paid UI is disabled.
- Email configured when required.
- Redis/cache configured when required.
- Production app/api URLs configured.
- Provider/integration statuses are known.
- Evidence source is live (not simulator).
- No simulator data represented as live.

## Truthfulness constraints
- Simulator evidence is labeled and cannot be treated as live readiness.
- Missing optional tables should return `unavailable`/`warn`, not crash endpoint.
- Secret values are never returned (booleans/status-only evidence).

## Remaining known gaps
- Some integration/export checks depend on table/service availability and may remain `unavailable` in partial deployments.
- Proof bundle capability is conditional and may be unavailable where not implemented.
