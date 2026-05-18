# Production Readiness Validation

Internal/admin readiness is exposed at `GET /admin/readiness` (authenticated workspace admin scope).

## Statuses
- `pass`: requirement satisfied.
- `warn`: non-blocking risk or intentionally disabled component.
- `fail`: blocking issue.
- `unavailable`: no trustworthy signal.

## Launch gates
- `ready_for_pilot`: false when blocking reasons exist (for example missing telemetry, unavailable evidence, or required provider not configured).
- `ready_for_paid_public_launch`: requires pilot readiness plus live evidence and configured billing/email.

## Truthfulness constraints
- Simulator evidence is labeled `source=simulator` and cannot become live pass evidence.
- Missing telemetry fails readiness; heartbeat alone is not sufficient.
- Missing dependencies are surfaced as fail/warn/unavailable (not healthy-by-default).
- Secrets are never returned; only safe booleans/status names are returned.

## Access control
- Endpoint uses existing authenticated workspace-admin gate (`_require_workspace_admin`).
- Remaining gap: role granularity depends on current workspace role model; no new public health exposure was added.
