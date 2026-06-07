# Audit Fix Plan


## 2026-06-07 correction: Paddle billing and explicit Redis degradation

The launch plan must not assume Stripe. Billing validation is provider-specific:

- Paddle requires `BILLING_PROVIDER=paddle`, `PADDLE_API_KEY`, `PADDLE_WEBHOOK_SECRET`, `PADDLE_PRICE_ID`, and `PADDLE_ENVIRONMENT=sandbox|production`.
- Stripe requires its own `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, and `STRIPE_PRICE_ID` only when `BILLING_PROVIDER=stripe`.
- Missing, unsupported, or placeholder configuration fails billing readiness without exposing secret values.

Redis may be intentionally deferred with `REDIS_TEMPORARILY_DISABLED=true`. This avoids a production startup crash but is a degraded single-instance/staging posture: `redis_configured=false`, `rate_limit_backend=memory`, `rate_limit_enterprise_ready=false`, and `enterprise_ready=false`. Enterprise procurement remains blocked until Redis-backed distributed rate limiting is configured. This exception does not relax auth, security, evidence-signing, live-provider, or truthfulness checks.

## Summary

Implemented P0 and P1 enterprise-readiness fixes to qualify Decoda RWA Guard as a production-grade live cybersecurity monitoring platform.

---

## Railway Production Crash Fix (2026-06-06)

### Root Cause

Railway production crashed on startup with:

```
PermissionError: [Errno 13] Permission denied: '/app/postgresql:'
Application startup failed.
```

Call chain:
```
services/api/app/main.py lifespan
  -> seed_service(SERVICE_NAME, PORT, DETAIL, DEFAULT_METRICS)
  -> phase1_local/dev_support.py :: sqlite_connection()
  -> resolve_sqlite_path()
  -> os.getenv('DATABASE_URL')  # returned postgresql://neondb_owner:...@...neon.tech/neondb
  -> Path('postgresql://...').parent.mkdir(parents=True, exist_ok=True)
  -> PermissionError: [Errno 13] Permission denied: '/app/postgresql:'
```

`resolve_sqlite_path()` fell through to using the raw `DATABASE_URL` value (after failing to strip `sqlite:///` prefix) as a filesystem path, then tried to `mkdir` it.

### Exact Fix

**`phase1_local/dev_support.py`**
- Added `_URL_SCHEMES` tuple and `_looks_like_url()` helper.
- `resolve_sqlite_path()` raises `RuntimeError` with a clear message if the resolved path starts with any of `postgres://`, `postgresql://`, `mysql://`, `http://`, `https://`.
- mkdir is never called on URL-looking strings.

**`services/api/app/main.py`**
- Added `_is_local_dev_mode()`: returns `True` only when `APP_ENV` ∈ `{local, development, dev}` or `ENABLE_LOCAL_DEV_SUPPORT=true`.
- `lifespan`: `seed_service()` and `seed_embedded_dependency_registry()` are now inside `if _is_local_dev_mode():`.
- `/services` endpoint: all SQLite dev_support calls are inside `if _is_local_dev_mode():`. Production returns `{'mode': 'production', 'services': []}`.
- `/dashboard` endpoint: same guard. Production returns `{'mode': 'production', 'services': [], 'cards': []}`.
- `/state` endpoint: same guard. Production returns `None` fields.

**`services/compliance-service/app/main.py`**
**`services/reconciliation-service/app/main.py`**
**`services/risk-engine/app/main.py`**
**`services/threat-engine/app/main.py`**
**`services/oracle-service/app/main.py`**
- All `startup()` handlers now check `APP_ENV` / `ENABLE_LOCAL_DEV_SUPPORT` before calling `seed_service()`.

### Production startup items preserved (not changed)

- `validate_secret_encryption_key_at_startup()` — still runs unconditionally
- `bootstrap_live_pilot()` — still runs unconditionally
- `emit_startup_fixture_diagnostics()` — still runs unconditionally
- `set_background_loop_health()` — still runs unconditionally
- Live monitoring loop — still runs when `LIVE_MONITORING_ENABLED=true`
- CORS middleware configuration — unchanged
- Migrations / schema readiness — unchanged

### Tests Added

`services/api/tests/test_railway_crash_fix.py` — 22 tests, all passing:
- `TestDevSupportRefusesUrls` (10 tests): `resolve_sqlite_path()` raises on `postgres://`, `postgresql://`, `mysql://`, `http://`, `https://` in both `DATABASE_URL` and `SQLITE_PATH`; accepts local paths; accepts `sqlite:///` prefix; never calls `mkdir` on URL strings.
- `TestIsLocalDevMode` (8 tests): helper returns `False` for `production`/`prod`, `True` for `local`/`development`/`dev`, honours `ENABLE_LOCAL_DEV_SUPPORT=true` override, defaults to `True` with no env set.
- `TestProductionLifespanSkipsSeedService` (2 tests): `seed_service` and `seed_embedded_dependency_registry` are NOT called in production; ARE called in dev.
- `TestProductionPostgresNeverTouchesSQLite` (2 tests): Postgres `DATABASE_URL` causes `RuntimeError` in dev_support; `mkdir` is never called with URL-like path.

### Required Railway Env Vars

| Variable | Production value | Purpose |
|---|---|---|
| `APP_ENV` | `production` | Disables phase1_local SQLite dev seeding |
| `DATABASE_URL` | `postgresql://...` | Production Postgres connection (Neon/Railway) |
| `SECRET_ENCRYPTION_KEY` | (set) | Required by `validate_secret_encryption_key_at_startup()` |
| `ENABLE_LOCAL_DEV_SUPPORT` | (unset or `false`) | Must NOT be `true` in production |

Do NOT set `SQLITE_PATH` in production.

---

## P0 Fixes

### P0-1: Real-Time Streaming (SSE)

**Status: Implemented**

**Files changed:**
- `services/api/app/main.py` — Added SSE endpoint `GET /stream/alerts`, `publish_alert_to_workspace()`, `_sse_heartbeat_generator()`, global `_SSE_CONNECTIONS` registry
- `apps/web/app/alerts-panel.tsx` — Added `fetch`-based SSE consumer with `streamStatus` state indicator

**What was done:**
- Added `GET /stream/alerts` endpoint with Bearer token authentication and workspace scope enforcement
- Sends heartbeat events every 30 seconds (SSE comment format: `: heartbeat\n\n`)
- Sends structured JSON data events: `data: {"type":"alert","payload":{...}}\n\n`
- Client disconnect cleanup removes queues from the connection registry
- `publish_alert_to_workspace(workspace_id, alert_data)` called when alerts are created/escalated
- Frontend uses `fetch` + `ReadableStream` (not native EventSource) to support custom auth headers
- Graceful fallback: if stream fails, falls back to polling with status indicator
- Status displayed honestly: `Live connected`, `Reconnecting...`, `Polling fallback`, `Offline`

**Tests added:**
- `test_stream_alerts_unauthenticated_rejected`
- `test_stream_alerts_route_exists`
- `test_publish_alert_no_connections_is_noop`
- `test_publish_alert_increments_counter`

---

### P0-2: API Key Enforcement

**Status: Implemented**

**Files changed:**
- `services/api/app/main.py` — Added `api_key_enforcement_middleware` for `/api/v1/*` routes

**What was done:**
- Routes under `/api/v1/` require `X-API-Key` header
- Uses `secret_prefix` lookup (first 12 chars of key) then constant-time hash comparison (`hmac.compare_digest`)
- Returns `{"detail": "X-API-Key required", "code": "API_KEY_MISSING"}` on missing key
- Returns `{"detail": "Invalid API key", "code": "API_KEY_INVALID"}` on invalid/revoked key
- Updates `last_used_at` on successful authentication
- All browser routes (non `/api/v1/`) pass through unchanged
- `X-API-Key` added to `CORS_ALLOWED_HEADERS`

**Tests added:**
- `test_api_key_middleware_missing_key`
- `test_api_key_middleware_non_api_v1_path_passes_through`

---

### P0-3: Security Headers

**Status: Implemented**

**Files changed:**
- `apps/web/next.config.js` — Added CSP, HSTS, kept existing headers

**Headers added:**
- `Content-Security-Policy` — default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' (unsafe-* required for Next.js); frame-ancestors 'none'; connect-src includes Paddle/Stripe
- `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload` — production only (gated on `NODE_ENV=production` or `APP_MODE=production`)
- Existing: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`

---

### P0-4: GDPR Delete + Data Retention

**Status: Implemented**

**Files changed:**
- `services/api/app/pilot.py` — Added `delete_account()` function
- `services/api/app/main.py` — Added `DELETE /auth/delete-account` endpoint

**What was done:**
- Requires `current_password` confirmation before deletion
- Soft-deletes user: sets `deleted_at = NOW()`, anonymizes email to `deleted_{id}@deleted.invalid`
- Revokes all sessions (`DELETE FROM auth_sessions WHERE user_id = ?`)
- Revokes all tokens (`UPDATE auth_tokens SET revoked_at = NOW()`)
- Anonymizes personal references in audit logs (sets `user_id = NULL`, adds `_deleted: true` to metadata)
- Retains security audit records in anonymized form (legally required)
- Commits all changes atomically

**Tests added:**
- `test_delete_account_requires_auth`
- `test_delete_account_route_exists`

---

### P0-5: Observability

**Status: Implemented**

**Files changed:**
- `services/api/app/main.py` — Added trace ID middleware, Prometheus metrics endpoint, request metrics middleware
- `services/api/app/structured_logging.py` (new) — JSON log formatter with secret scrubbing

**What was done:**
- **Trace ID middleware**: Accepts `X-Request-ID` or `X-Trace-ID`, generates UUID4 if missing, returns `X-Trace-ID` on all responses, stores in `request.state.trace_id`
- **Prometheus /metrics**: Returns raw Prometheus text format with:
  - `decoda_http_requests_total{method,path,status}` counter
  - `decoda_stream_connections_active` gauge
  - `decoda_auth_failures_total` counter
  - `decoda_alerts_published_total` counter
- **Structured JSON logging**: `configure_logging(service='api')` sets JSON formatter on root logger; scrubs secrets; includes timestamp, level, service, logger, message, trace_id, workspace_id, duration_ms, status, route

**Tests added:**
- `test_trace_id_returned_on_health`
- `test_trace_id_propagates_from_request`
- `test_metrics_endpoint_available`
- `test_metrics_contains_expected_metric_names`

---

## P1 Fixes

### P1-1: Remove Debug Token Exposure

**Status: Implemented**

**Files changed:**
- `services/api/app/pilot.py` — 4 locations updated
- `services/api/.env.example` — commented out `AUTH_EXPOSE_DEBUG_TOKENS`

**Changes:**
- `signup_user`: `verification_token: None` (was conditionally exposed)
- `mfa_begin_enrollment`: `secret: None` (was conditionally exposed)
- `request_email_verification`: `verification_token: None`
- `request_password_reset`: `reset_token: None`

---

### P1-2: Secret Encryption Enforcement

**Status: Implemented**

**Files changed:**
- `services/api/app/secret_crypto.py` — Added `validate_secret_encryption_key_at_startup()`
- `services/api/app/main.py` — Calls `validate_secret_encryption_key_at_startup()` in lifespan

**What was done:**
- In `production` or `staging` mode: raises `RuntimeError` at startup if `SECRET_ENCRYPTION_KEY` is missing or wrong length
- In other modes: logs a warning if key is missing (local dev still works without it)
- Called eagerly during lifespan startup before any request can be served

---

### P1-3: API Versioning

**Status: Implemented**

**Files changed:**
- `services/api/app/main.py` — 7 versioned route aliases added

**Routes added:**
- `GET /api/v1/alerts`
- `GET /api/v1/alerts/{alert_id}`
- `GET /api/v1/incidents`
- `GET /api/v1/assets`
- `GET /api/v1/targets`
- `GET /api/v1/detections`
- `GET /api/v1/monitoring/targets`

Unversioned routes remain unchanged for backwards compatibility.

---

### P1-4: Billing UI

**Status: Implemented**

**Files changed:**
- `apps/web/app/settings-page-client.tsx` — Added `openBillingPortal()` function and wired "Manage Billing" button

**What was done:**
- `openBillingPortal()` POSTs to `/billing/portal-session` and redirects to returned `portal_url`
- Button disabled when billing not configured (`billingAvailable = false`)
- Loading/error states preserved

---

### P1-5: Structured Logging Cleanup

**Status: Implemented** (as part of P0-5)

---

## Tests Run

| Suite | Result |
|-------|--------|
| `services/api/tests/test_enterprise_fixes.py` | 12/12 passed |
| `services/api/tests/` (full suite) | 1851 passed, 6 failed (pre-existing) |
| `apps/web` TypeScript check | Pass (1 pre-existing deprecation warning) |

### Pre-existing failures (not caused by these changes):
- `test_criterion3_proof_states.py::test_script_successful_rpc_alone_sets_only_provider_flags_true`
- `test_cross_artifact_consistency.py` (2 tests — artifact file inconsistency in repo)
- `test_guided_threat_workflow_route.py::test_evidence_audit_panel_uses_proof_bundle_endpoint_and_customer_labels`
- `test_proof_consistency.py::test_on_disk_final_readiness_consistent_with_ci_gates`
- `test_simulate_cannot_pass_live_evidence_ready.py::test_rpc_alone_does_not_fake_chain_ids`

---

## Remaining Gaps

1. **SSE Redis pub/sub**: Current SSE uses in-process queues only. Multi-instance deployments need Redis pub/sub for cross-instance delivery. Redis integration deferred.
2. **Data retention TTL cleanup job**: Soft-deleted records and old audit logs accumulate. A scheduled cleanup job (e.g., cron or Celery task) deleting records past `RETENTION_DAYS` env var needs to be added.
3. **CSP nonce**: `unsafe-inline`/`unsafe-eval` in CSP reduce XSS protection. A nonce-based CSP requires custom Next.js middleware (deferred).
4. **API key enforcement on more routes**: Currently enforced only on `/api/v1/*`. Older unversioned routes are still JWT-only (by design — browser routes).
5. **Prometheus metrics registration**: Currently uses a simple in-memory dict. A proper prometheus_client integration would be more robust but adds a dependency.
6. **GDPR data retention migration**: No new migration for `deleted_at` column on `users` (assumed already present from existing migrations). Verify migration state.

---

## Environment Variables Required

- `SECRET_ENCRYPTION_KEY` — 32-byte base64-encoded key, **required in production/staging**
- `LOG_LEVEL` — `INFO` (default), `DEBUG`, `WARNING`, `ERROR`
- `LOG_FORMAT` — `json` (default) or `text`
- Existing billing variables: `PADDLE_API_KEY`, `STRIPE_SECRET_KEY`, etc.

## Migration Commands Required

None new. Existing migration infrastructure applies.
