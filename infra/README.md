# Infrastructure Inventory — Decoda RWA Guard

**Audience**: Operators, DevSecOps, and on-call engineers  
**Related docs**: [`docs/DISASTER_RECOVERY_AND_DATA_GOVERNANCE.md`](../docs/DISASTER_RECOVERY_AND_DATA_GOVERNANCE.md) · [`docs/OPERATIONS_RUNBOOK.md`](../docs/OPERATIONS_RUNBOOK.md) · [`docs/runbooks/incident-response.md`](../docs/runbooks/incident-response.md)

---

## 1. Service Topology

```
┌──────────────────────────────────────────────────────┐
│  Users / customers (browser)                         │
└────────────────┬─────────────────────────────────────┘
                 │ HTTPS
┌────────────────▼─────────────────────────────────────┐
│  Web (Next.js)         apps/web/                     │
│  Hosting: Vercel                                     │
│  Env: NEXT_PUBLIC_*  API_URL  NEXTAUTH_*             │
└────────────────┬─────────────────────────────────────┘
                 │ HTTPS REST / SSE
┌────────────────▼─────────────────────────────────────┐
│  API (FastAPI / Python)  services/api/               │
│  Hosting: Railway                                    │
│  Process: uvicorn (API) + monitoring worker          │
└───┬──────────────────┬────────────────┬──────────────┘
    │                  │                │
    ▼                  ▼                ▼
PostgreSQL          Redis           Object storage
(Neon / hosted)   (Redis Cloud /   (S3 / WORM-compatible)
                   Railway Redis)
```

---

## 2. Required Services

### 2.1 API service

| Property | Value |
|---|---|
| Runtime | Python 3.12 / FastAPI / uvicorn |
| Hosting | Railway (container) |
| Source | `services/api/` |
| Health | `GET /health` (no auth) · `GET /health/readiness` (Bearer) |
| Metrics | `GET /health/details` (Bearer) |
| Startup | `python -m uvicorn services.api.app.main:app` |
| Migrations | `python scripts/migrate.py` (idempotent, runs at startup) |

### 2.2 Web frontend

| Property | Value |
|---|---|
| Runtime | Node 22 / Next.js |
| Hosting | Vercel (production) |
| Source | `apps/web/` |
| Build | `npm run build` |
| Health | Vercel deployment status |

### 2.3 PostgreSQL (Neon)

| Property | Value |
|---|---|
| Provider | Neon (recommended) or any hosted PostgreSQL ≥ 15 |
| Purpose | Primary data store: users, workspaces, assets, alerts, audit_logs, telemetry |
| Connection | `DATABASE_URL` (connection string, pooled) |
| Migrations | Forward-only via `scripts/migrate.py` |
| Backups | Provider-managed daily snapshots; PITR target 1 hour |
| Sizing | Start: 0.25 CU / 512 MB; scale based on telemetry volume |

### 2.4 Redis

| Property | Value |
|---|---|
| Provider | Redis Cloud, Railway Redis, or self-hosted Redis ≥ 7 |
| Purpose | SSE alert fan-out (Redis Streams), distributed rate-limiting, session blacklist |
| Connection | `REDIS_URL` (redis:// or rediss://) |
| Fail-closed | API refuses connections in `LIVE_MODE_ENABLED=true` if `REDIS_URL` absent |
| Sizing | Start: 50 MB; SSE workspaces × stream retention determines growth |

### 2.5 Object storage (export / WORM)

| Property | Value |
|---|---|
| Provider | AWS S3, GCS, or S3-compatible (MinIO for self-hosted) |
| Purpose | Signed export bundles, proof chains, compliance reports |
| Connection | `EXPORT_STORAGE_URL` + `EXPORT_STORAGE_KEY` + `EXPORT_STORAGE_SECRET` |
| Bucket policy | WORM / Object Lock recommended for legal-hold evidence |
| Optional | System works without export storage; DB copy is authoritative fallback |

---

## 3. Secrets and Environment Variables

All secrets are injected as environment variables; none are committed to the repository.  
Use Railway variables (production), Vercel environment (frontend), and `.env.local` (local dev).

### 3.1 API service (`services/api/`)

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes (live mode) | PostgreSQL connection string |
| `REDIS_URL` | Yes (live mode) | Redis connection string |
| `APP_SECRET_KEY` | Yes | JWT signing secret (≥ 32 random bytes, base64 or hex) |
| `LIVE_MODE_ENABLED` | Yes | `true` enables production guard (requires DB + Redis) |
| `APP_ENV` | Yes | `production` / `staging` / `test` |
| `APP_MODE` | Yes | `live` / `test` |
| `EVM_RPC_URL_<CHAIN>` | Yes (monitoring) | RPC endpoint per chain (e.g. `EVM_RPC_URL_ETHEREUM`) |
| `EXPORT_STORAGE_URL` | No | S3/GCS bucket URL for signed exports |
| `EXPORT_STORAGE_KEY` | No | Storage access key |
| `EXPORT_STORAGE_SECRET` | No | Storage secret key |
| `BILLING_PROVIDER` | No | `stripe` or `paddle` |
| `STRIPE_SECRET_KEY` | If billing=stripe | Stripe secret key |
| `STRIPE_WEBHOOK_SECRET` | If billing=stripe | Stripe webhook signing secret |
| `PADDLE_API_KEY` | If billing=paddle | Paddle API key |
| `PADDLE_WEBHOOK_SECRET` | If billing=paddle | Paddle webhook signing secret |
| `RETENTION_DAYS` | No | Data retention window in days (default: 90) |
| `PROBE_TOKEN` | No | Token for `/health/readiness` health checks |
| `MANAGED_SIGNING_KEY` | No | Key for managed evidence signing (auto-generated if absent) |

### 3.2 Web frontend (`apps/web/`)

| Variable | Required | Description |
|---|---|---|
| `API_URL` | Yes | Internal URL of the API service |
| `NEXTAUTH_URL` | Yes | Public base URL of the web app |
| `NEXTAUTH_SECRET` | Yes | NextAuth.js session encryption secret |
| `NEXT_PUBLIC_API_URL` | Yes | Public API URL (used by browser) |
| `NEXT_PUBLIC_LIVE_MODE_ENABLED` | Yes | `true` / `false` — must match API setting |
| `NEXT_PUBLIC_BILLING_PROVIDER` | No | `stripe` / `paddle` (shown in UI) |
| `STRIPE_PUBLISHABLE_KEY` | If billing=stripe | Stripe publishable key |
| `PADDLE_CLIENT_TOKEN` | If billing=paddle | Paddle client token |

### 3.3 CI / Release pipeline (GitHub Actions)

| Secret | Workflow | Description |
|---|---|---|
| `RELEASE_PROBE_URL` | `release-attestation.yml` | URL to probe after release |
| `RELEASE_PROBE_TOKEN` | `release-attestation.yml` | Bearer token for release probe |
| `RELEASE_ATTESTATION_SIGNING_KEY` | `release-attestation.yml` | Key for release attestation signature |

> **PR CI** (`ci-pr.yml`) requires **no** production secrets.

---

## 4. Network and Access Controls

- API service: public HTTPS only; no direct database port exposure.
- Database: private network / VPC peering with API service; no public endpoint required.
- Redis: private network with API service; TLS (`rediss://`) in production.
- Admin endpoints (`/admin/*`, `/ops/*`): protected by admin-scoped Bearer token.
- SSE stream (`/stream/alerts`): workspace-scoped JWT; requires valid session.
- CORS: configured per `APP_ENV`; production allows only the configured web origin.

---

## 5. Scaling and Redundancy

| Component | Horizontal scaling |
|---|---|
| API | Stateless; scale via Railway replicas. SSE fan-out is Redis-backed so all replicas share the stream. |
| Monitoring worker | Single instance per deployment; scheduled via internal scheduler or Railway cron. |
| PostgreSQL | Use Neon autoscaling or read replicas for read-heavy analytics queries. |
| Redis | Redis Cluster or Redis Cloud multi-AZ for HA. |
| Web | Vercel edge-replicated by default. |

---

## 6. Deployment Checklist

Before promoting to production:

- [ ] `DATABASE_URL` points to production database (not dev/staging)
- [ ] `REDIS_URL` set and reachable (`redis-cli -u $REDIS_URL PING`)
- [ ] `APP_SECRET_KEY` is a unique secret (not shared across environments)
- [ ] `LIVE_MODE_ENABLED=true` confirmed for live deployment
- [ ] `APP_ENV=production` set
- [ ] EVM RPC URLs configured for each monitored chain
- [ ] `GET /health/readiness` returns `status=ready` after deploy
- [ ] Export storage bucket exists with WORM/Object Lock policy (if using)
- [ ] Billing provider webhook endpoint registered (if billing enabled)
- [ ] Monitoring worker deployed and heartbeat confirmed: `GET /ops/monitoring/health`
- [ ] Retention worker enabled (RETENTION_DAYS set): `GET /ops/retention/health`

---

## 7. Terraform / IaC Roadmap

Full Terraform is not yet provided. Priority order for IaC coverage:

1. **Neon PostgreSQL project + branch** — database provisioning and connection string output
2. **Redis Cloud subscription + database** — or Railway Redis add-on via Railway API
3. **S3 bucket + Object Lock policy** — export evidence bucket with WORM policy
4. **Railway project + services** — API and monitoring worker services with env var references
5. **Vercel project + env** — frontend deployment with environment variable mapping
6. **GitHub Actions secrets** — bootstrap via `gh secret set` from a one-time provisioning script

Contributions welcome: `infra/terraform/` is the intended directory for Terraform modules.

---

## 8. Local Development

```bash
# Minimal local stack (SQLite, no Redis, no live monitoring)
cp .env.example .env.local
# Edit .env.local: set APP_ENV=test, LIVE_MODE_ENABLED=false

# Backend
pip install -r requirements-local.txt
python -m uvicorn services.api.app.main:app --reload --port 8000

# Frontend (separate terminal)
npm install
npm run dev
```

For local Redis:
```bash
docker run -d -p 6379:6379 redis:7-alpine
# Then set REDIS_URL=redis://localhost:6379 in .env.local
```

For local PostgreSQL:
```bash
docker run -d -p 5432:5432 -e POSTGRES_DB=decoda -e POSTGRES_PASSWORD=dev postgres:15-alpine
# Then set DATABASE_URL=postgresql://postgres:dev@localhost:5432/decoda in .env.local
```

Verify the stack:
```bash
curl http://localhost:8000/health
python -m pytest services/api/tests/ -q -m "not integration" --timeout=60
```
