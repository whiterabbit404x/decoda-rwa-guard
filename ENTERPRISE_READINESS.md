# Enterprise Readiness

This document describes the enterprise-grade controls implemented in Decoda RWA Guard.

---

## Real-Time Streaming

**Endpoint:** `GET /stream/alerts`

**Design:**
- Server-Sent Events (SSE) via `StreamingResponse` with `media_type='text/event-stream'`
- Authentication: Bearer token (`Authorization` header) and workspace scope (`X-Workspace-Id` header)
- Tenant isolation: each workspace gets its own set of queues; events never cross workspace boundaries
- Heartbeat: SSE comment ``: heartbeat`` every 30 seconds to keep connections alive
- Event format: `data: {"type":"alert","payload":{...}}\n\n`
- `X-Trace-ID` included in SSE response headers for observability
- Client disconnect triggers cleanup of the queue from the registry
- Graceful degradation: if no connections exist, `publish_alert_to_workspace()` is a no-op

**Frontend:**
- Uses `fetch` + `ReadableStream` (not native `EventSource`) to support custom auth headers
- Status shown honestly: `Live connected` / `Reconnecting...` / `Polling fallback` / `Offline`
- Falls back to 30s polling if SSE fails or is unavailable
- Never shows "Live connected" when using polling

**Known limitation:** In-process queues only. Multi-process/multi-instance deployments require Redis pub/sub for cross-instance event delivery.

---

## API Key Enforcement Policy

**Routes requiring X-API-Key:**
- All routes under `/api/v1/` (integration/machine access)

**Routes using browser JWT/session auth:**
- All other routes (unchanged)

**Public/exempt routes (no auth required):**
- `/health`, `/ops/*`, `/auth/*` (login/signup/reset), `/billing/webhooks/*`, `/api/billing/*`

**Implementation:**
- `api_key_enforcement_middleware` in `main.py` runs before route resolution for `/api/v1/*`
- Key lookup: `SELECT ... FROM api_keys WHERE secret_prefix = ? AND revoked_at IS NULL`
- Hash comparison: `hmac.compare_digest()` (constant-time, prevents timing attacks)
- `last_used_at` updated on successful auth
- Keys are stored as scrypt-hashed values; only the first 12 chars (prefix) stored in plaintext for lookup
- Revoked or expired keys are rejected
- Invalid or missing key returns `401` with `{"code": "API_KEY_MISSING"}` or `{"code": "API_KEY_INVALID"}`

**Key format:** `decoda_wk_<32 random URL-safe bytes>` (generated via `secrets.token_urlsafe(32)`)

---

## Security Headers

All HTTP responses from the Next.js frontend include:

| Header | Value |
|--------|-------|
| `Content-Security-Policy` | `default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; connect-src 'self' https://*.paddle.com https://*.stripe.com wss: ws:; frame-src 'self' https://js.stripe.com https://hooks.stripe.com https://checkout.paddle.com https://buy.paddle.com; frame-ancestors 'none'; base-uri 'self'; form-action 'self'` |
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains; preload` (production only) |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` |

**Note on CSP:** `unsafe-inline` and `unsafe-eval` are required for Next.js. A nonce-based CSP would require custom middleware and is planned for a future iteration. The CSP still blocks most XSS vectors via `default-src 'self'` and `frame-ancestors 'none'`.

---

## GDPR Delete / Data Retention

**Endpoint:** `DELETE /auth/delete-account`

**Process:**
1. Authenticates request via Bearer token
2. Requires `current_password` in request body (password verification prevents CSRF-based deletion)
3. Soft-deletes user: sets `deleted_at = NOW()`, anonymizes email to `deleted_{user_id}@deleted.invalid`, sets `full_name = 'Deleted User'`
4. Revokes all active sessions (`DELETE FROM auth_sessions WHERE user_id = ?`)
5. Revokes all tokens (`UPDATE auth_tokens SET revoked_at = NOW() WHERE user_id = ?`)
6. Anonymizes audit log entries: sets `user_id = NULL`, adds `{"_deleted": true}` to metadata — security evidence is retained in anonymized form for legal compliance
7. All changes committed atomically

**Data retention:**
- Personal data: Deleted/anonymized immediately on account deletion
- Security evidence (audit logs): Retained in anonymized form (legally required)
- Sessions: Immediately revoked

**Known gap:** A TTL cleanup job for old soft-deleted records and stale audit logs is not yet implemented. Add a scheduled task using `RETENTION_DAYS` env var (default: 365).

---

## Observability

### Request Trace IDs

Every HTTP response includes `X-Trace-ID`. If the incoming request provides `X-Request-ID` or `X-Trace-ID`, the same value is echoed back (allowing distributed tracing). Otherwise, a UUID4 is generated and used.

The trace ID is stored in `request.state.trace_id` and available to all downstream handlers and log records.

### Prometheus Metrics

**Endpoint:** `GET /metrics` (unauthenticated, Prometheus scrape format)

| Metric | Type | Description |
|--------|------|-------------|
| `decoda_http_requests_total{method,path,status}` | Counter | Total HTTP requests by method, path, and status code |
| `decoda_stream_connections_active` | Gauge | Current active SSE stream connections |
| `decoda_auth_failures_total` | Counter | API key authentication failures |
| `decoda_alerts_published_total` | Counter | Total alerts published to SSE connections |

**Security note:** The `/metrics` endpoint does not expose secrets, user data, or workspace information. In production, restrict access to internal network / monitoring subnet via reverse proxy (e.g., nginx `allow 10.0.0.0/8; deny all;`).

### Structured JSON Logging

Format (when `LOG_FORMAT=json`, which is the default):

```json
{
  "timestamp": "2026-06-06T12:00:00",
  "level": "INFO",
  "service": "api",
  "logger": "services.api.app.main",
  "message": "...",
  "trace_id": "...",
  "workspace_id": "...",
  "duration_ms": 42,
  "status": 200,
  "route": "/alerts"
}
```

Secret scrubbing: any key containing `password`, `secret`, `token`, `key`, `authorization`, `credential`, `api_key`, `private_key` is replaced with `"***"` in log output.

**Configuration:**
- `LOG_LEVEL` — `INFO` (default), `DEBUG`, `WARNING`, `ERROR`
- `LOG_FORMAT` — `json` (default) or `text`

---

## Known Limitations

1. **SSE in multi-instance deployments**: In-process queue registry. Redis pub/sub required for horizontal scaling.
2. **CSP unsafe-inline**: Required by Next.js. Nonce-based CSP planned.
3. **Data retention job**: Soft-deleted records accumulate. Scheduled cleanup job needed.
4. **/metrics auth**: Unauthenticated by design (Prometheus standard). Must be network-isolated in production.
5. **API key enforcement scope**: Only covers `/api/v1/*` routes. Legacy unversioned routes use JWT-only auth.
6. **TOTP secret in MFA enrollment response**: Returns `null` now that debug exposure is removed. Clients must parse from `otpauth_uri` instead (standard practice).
