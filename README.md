# Phase 1 Tokenized Treasury Risk-Control Monorepo

This repo now supports a reproducible local Phase 1 workflow without Docker as the primary path. The stable Phase 1 risk-engine remains intact, Feature 2 adds the `threat-engine` service for explainable zero-day exploit mitigation and treasury-token market anomaly detection, Feature 3 adds the `compliance-service` for sovereign-grade compliance wrappers, geopatriation controls, and governance actions, and Feature 4 expands the existing `reconciliation-service` into an interoperability and systemic resilience slice for deterministic cross-chain reconciliation, backstop controls, and local incident logging.

## Public self-serve SaaS mode (Railway + Vercel + Neon)

The repo now supports a production-oriented **public self-serve SaaS mode** alongside the existing local/demo mode. Demo endpoints and UI still work, but authenticated live-mode actions persist workspace-scoped data through `services/api` into Neon Postgres. The backend deployment model remains the same: Railway still runs the existing API Dockerfile, and Vercel still hosts the Next.js frontend.

### Pilot architecture changes

- `services/api` remains the gateway and now also owns auth, workspace membership, audit logging, and persisted pilot records.
- Existing backend services (`risk-engine`, `threat-engine`, `compliance-service`, `reconciliation-service`) remain computation/demo providers; the gateway persists live summaries and workflow records after it calls them.
- Live mode writes Neon-backed `users`, `workspaces`, `workspace_members`, `analysis_runs`, `alerts`, `governance_actions`, `incidents`, and `audit_logs`.
- Demo mode remains separate from live mode. Existing dashboard fallback/demo data never mixes with live customer records.

### Live-mode environment variables

#### Railway / API

- `LIVE_MODE_ENABLED=true`
- `DATABASE_URL=postgresql://...` (Neon connection string, with `sslmode=require`)
- `AUTH_TOKEN_SECRET=<long-random-secret>`
- `CORS_ALLOWED_ORIGINS=http://localhost:3000,https://<your-vercel-app>.vercel.app`
- Existing downstream service URLs (`RISK_ENGINE_URL`, `THREAT_ENGINE_URL`, `COMPLIANCE_SERVICE_URL`, `RECONCILIATION_SERVICE_URL`)

#### Vercel / web

| Environment | Required vars | Notes |
| --- | --- | --- |
| Production | `NEXT_PUBLIC_LIVE_MODE_ENABLED=true` or `false`; `API_URL=https://<your-railway-api>.up.railway.app` (preferred) or `NEXT_PUBLIC_API_URL=https://<your-railway-api>.up.railway.app`; optional `NEXT_PUBLIC_API_TIMEOUT_MS=5000` | Production remains strict: `next build` fails before app code runs if `NEXT_PUBLIC_LIVE_MODE_ENABLED` is missing/invalid or if both API URL vars are absent. |
| Preview | `API_URL=https://<preview-or-shared-railway-api>.up.railway.app` (preferred) or `NEXT_PUBLIC_API_URL=https://<preview-or-shared-railway-api>.up.railway.app`; `NEXT_PUBLIC_LIVE_MODE_ENABLED=true` or `false` is recommended | Preview builds now pass when `API_URL` exists even if `NEXT_PUBLIC_API_URL` is absent, because the same-origin auth proxy prefers `API_URL`. Preview still fails fast if both API URL vars are missing, and the build log now tells operators exactly which Vercel environment setting to fix. |
| Development | `NEXT_PUBLIC_LIVE_MODE_ENABLED=false` (or `true` if you are exercising pilot mode locally), `NEXT_PUBLIC_API_URL=http://127.0.0.1:8000`, optional `NEXT_PUBLIC_API_TIMEOUT_MS=5000` | Local development still falls back to localhost defaults, but explicitly setting the vars keeps local behavior aligned with Vercel. |

Recommended Vercel project settings:

- **Root Directory:** `apps/web`
- **Framework Preset:** Next.js
- **Install Command:** repo-root install is fine, but the web project itself must still build from `apps/web` inside the monorepo.

Operator checklist for Vercel build validation:

1. Set **Root Directory = `apps/web`**.
2. For **Preview**, configure at least one backend URL in Vercel. `API_URL` is preferred because the same-origin auth proxy reads the server-side value first; `NEXT_PUBLIC_API_URL` is an acceptable fallback when a public browser URL is required.
3. For **Production**, configure both a valid `NEXT_PUBLIC_LIVE_MODE_ENABLED` value (`true` or `false`) and at least one backend URL (`API_URL` preferred, `NEXT_PUBLIC_API_URL` fallback).
4. If a preview build fails, read the build log block from `apps/web/build/vercel-build-validation.js`: it prints `vercelEnv`, branch, commit SHA, cwd, expected root directory, and found/missing status for `NEXT_PUBLIC_LIVE_MODE_ENABLED`, `API_URL`, and `NEXT_PUBLIC_API_URL`.

#### PR preview troubleshooting

- **Root Directory** must be `apps/web` so the monorepo build runs the intended Next.js app and validation script.
- Preview vars must exist on the **same linked Vercel project** connected to the GitHub repo; setting them on a different Vercel project will not unblock the PR deployment.
- The same-origin auth proxy prefers `API_URL`; use `NEXT_PUBLIC_API_URL` only when the browser truly needs a separate public backend origin.
- Old preview URLs may be stale if newer preview deploys fail, so always compare the failed build log's branch/commit block before debugging runtime behavior.

### Migrations and seed commands

Run these from the repo root after setting `DATABASE_URL` and `AUTH_TOKEN_SECRET` for the API service:

```bash
python services/api/scripts/migrate.py
python services/api/scripts/seed.py --pilot-demo
```

The pilot demo seed creates a workspace-scoped demo account in Postgres while preserving the original local SQLite registry seed for demo mode. You can override the seeded credentials with `--demo-email`, `--demo-password`, `--demo-workspace`, and `--demo-full-name`.

### Railway deploy / update flow

Run **two Railway services** from this monorepo for production monitoring:

1. Create `decoda-api` service:
   - Dockerfile path: `services/api/Dockerfile`
   - Root/build context: repository root
   - `APP_START_COMMAND` (optional, default): `uvicorn services.api.app.main:app --host 0.0.0.0 --port ${PORT:-8000}`
   - Optional startup migration toggle: `RUN_MIGRATIONS_ON_STARTUP=true` **only on this API service** when you intentionally want API boot to run migrations.
2. Create `decoda-monitoring-worker` service from the same repo:
   - Dockerfile path: `services/api/Dockerfile`
   - Root/build context: repository root
   - Start command env override:
     - `APP_START_COMMAND=python -m services.api.app.run_monitoring_worker --worker-name railway-monitoring-worker --interval-seconds 15 --limit 50`
   - Keep `RUN_MIGRATIONS_ON_STARTUP=false` here so worker boot never attempts schema migration.
3. Keep the Docker build context at the repo root so `services/api/Dockerfile` can `COPY` sibling fixture folders such as `services/risk-engine/data` and `services/reconciliation-service/data`.
4. Set Railway env vars from `services/api/.env.example` on **both** services, including monitoring worker vars (`MONITORING_WORKER_NAME`, `MONITORING_WORKER_INTERVAL_SECONDS`, `MONITORING_WORKER_LIMIT`, optional `MONITORING_WORKER_HEARTBEAT_TTL_SECONDS`).
5. Run migrations in exactly one place (`python services/api/scripts/migrate.py`) using Railway shell or a one-off command tied to the API service image/env. Do not add `migrate.py` to worker/event-watcher startup commands.
6. Optionally run `python services/api/scripts/seed.py --pilot-demo` once for a demo tenant.
7. Verify worker heartbeat via `GET /ops/monitoring/health` on the API service.
8. For manual one-shot verification in Railway shell, run `python -m services.api.app.run_monitoring_worker --once` (or `python services/api/scripts/run_monitoring_worker.py --once`).

### Vercel setup flow

1. Keep deploying `apps/web` on Vercel.
2. Set Vercel env vars from `apps/web/.env.example`.
3. Redeploy after updating `API_URL`, `NEXT_PUBLIC_API_URL`, or `NEXT_PUBLIC_LIVE_MODE_ENABLED`.
4. Use `/sign-up`, `/sign-in`, and `/workspaces` for live pilot onboarding while `/` continues to expose the existing dashboard.

### Why preview can fail while production works

Preview deploys are more likely to fail than production in this monorepo because Vercel treats **environment scopes**, **root-directory settings**, and **branch-specific config** separately:

- Production can keep working while Preview is broken if Preview is missing both `API_URL` and `NEXT_PUBLIC_API_URL`, or if Preview has an invalid `NEXT_PUBLIC_LIVE_MODE_ENABLED` value. Missing `NEXT_PUBLIC_LIVE_MODE_ENABLED` alone now warns instead of blocking the deploy.
- Production can keep working while Preview is broken if the Vercel project root points somewhere other than `apps/web`, because the monorepo build will resolve a different working directory during `next build`.
- Preview can fail at build time even before auth traffic reaches Railway, while production appears healthy, because `apps/web/next.config.js` runs `build/vercel-build-validation.js` during `next build` and Vercel stops the deployment before app code executes.
- Preview can also reach runtime with the wrong backend target if branch-specific env vars drift. In that case, `/api/build-info` tells operators whether the issue is the build, the preview environment, or the resolved runtime config.

Fast operator checks:

1. Confirm the Vercel project **Root Directory** is `apps/web`.
2. Confirm the Preview environment in Vercel has at least one of `API_URL` / `NEXT_PUBLIC_API_URL`; `API_URL` is preferred because the same-origin auth proxy uses the server-side value first.
3. If the build logs warn about `NEXT_PUBLIC_LIVE_MODE_ENABLED`, add it explicitly so preview/demo mode and live-mode behavior are obvious to operators.
4. Open `/api/build-info` on the preview deployment and verify `vercelEnv`, `branch`, `commitSha`, and the runtime config summary.
5. If `/api/build-info` is healthy but auth still fails, the next place to check is the backend API / Railway deployment rather than the same-origin auth proxy.

### External requirements before enterprise claims

- Email verification, password reset, and server-side session invalidation.
- TOTP MFA, distributed/shared-store auth rate limiting, background workers, webhooks, and billing automation are still planned before GA.
- Stronger distributed rate limiting / shared cache instead of in-memory auth throttling.
- Richer RBAC enforcement across every workflow action.
- Background jobs, webhooks, and more granular per-record dashboards instead of summary persistence only.
- Managed observability, secret rotation, and tenant billing / provisioning workflows.


## No-billing pilot launch runbook (production-polished mode)

Use this when you want a credible customer-facing deployment without checkout enabled.

Prerequisites for reproducible proof runs:

- Node.js 20+ and npm 10+.
- Python 3.11+ with `pytest` available in your active environment.
- Network access for first-time Playwright Chromium install (only required when browser smoke checks must execute locally).

1. Configure API env with: `LIVE_MODE_ENABLED=true`, `DATABASE_URL`, `AUTH_TOKEN_SECRET`, `APP_PUBLIC_URL`, and `BILLING_PROVIDER=none`.
2. Run migrations: `python services/api/scripts/migrate.py`.
3. Seed optional pilot demo user: `python services/api/scripts/seed.py --pilot-demo`.
4. Start API + worker processes (Railway) and web app (Vercel).
5. Deterministic install from a fresh checkout: `npm ci` (or `npm run install:clean`).
6. Optional local browser bootstrap (recommended for full local smoke coverage): `make install-web-test-runtime`.
7. Validate web build from that clean install: `npm run build:web`.
8. Validate pilot launch posture: `make validate-no-billing-launch` (or `npm run validate:no-billing-launch`).
9. Generate a reproducible proof-run artifact bundle (includes command logs + summary): `npm run proof:no-billing-launch`.
   - Artifacts are written under `artifacts/launch-proof/<timestamp>/`.
   - Required commands in this proof runner are exactly:
     - `npm ci`
     - `npm run build:web`
     - `make validate-no-billing-launch`
   - Optional step `python scripts/staging/run_evidence_flow.py` runs automatically only when required `STAGING_*` vars are present; otherwise it is recorded as an explicit `SKIP`.
10. Run staging evidence flow when staging credentials are available:
   - `export STAGING_BASE_URL=...`
   - `export STAGING_API_URL=...`
   - `export STAGING_EVIDENCE_EMAIL=...`
   - `export STAGING_EVIDENCE_PASSWORD=...`
   - `npm run evidence:staging`

`make validate-no-billing-launch` intentionally treats `BILLING_PROVIDER=none` as pilot-valid. It still fails for real blockers (for example broken API startup checks, broken auth/runtime, dependency drift, or failed web build).

Expected success signal for `make validate-no-billing-launch`:

- JSON summary with `"ok": true`.
- Release summary categories reported as `PASS` (with optional `SKIP` only for documented pilot allowances).

Troubleshooting:

- If validation fails at `node_bootstrap_prerequisites`, run `npm ci` and retry.
- If Playwright runtime is missing, run `make install-web-test-runtime` (or `npx playwright install chromium`) and retry.
- If Playwright browser download is blocked by network policy, no-billing pilot mode may record that browser step as `SKIP`, but build + API/provider checks must still pass.

This mode is **pilot-ready** and **marketing-ready**. Broad paid self-serve remains out of scope until billing provider credentials and checkout workflows are enabled in deployment.

## Self-serve public beta foundations (this pass)

The API now includes real self-serve security foundations intended for public-beta usage:

- **Transactional email provider abstraction** (`EMAIL_PROVIDER=console|resend`) for verification/reset flows.
- **TOTP MFA backend flows**:
  - `POST /auth/mfa/enroll`
  - `POST /auth/mfa/confirm`
  - `POST /auth/mfa/complete-signin`
  - `POST /auth/mfa/disable`
- **Session controls**:
  - `GET /auth/sessions`
  - `POST /auth/sessions/revoke`
  - existing `POST /auth/signout-all`
- **Background jobs foundation**:
  - database-backed `background_jobs` queue
  - `python services/api/scripts/run_worker.py` worker loop
  - `POST /ops/jobs/run` operator one-shot job execution
- **Distributed auth rate limiting** with Redis when `REDIS_URL` is configured, with safe in-memory fallback for local/dev only.
- **Startup runtime validation** for production-like environments (fails fast for missing critical auth config, warns on unsafe production settings).

### New API environment variables

- `APP_ENV=development|production`
- `APP_PUBLIC_URL=https://your-app.example.com`
- `AUTH_EXPOSE_DEBUG_TOKENS=false`
- `MFA_ISSUER=Decoda RWA Guard`
- `EMAIL_PROVIDER=console|resend`
- `EMAIL_FROM=no-reply@decoda.app`
- `EMAIL_BRAND_NAME=Decoda RWA Guard`
- `EMAIL_RESEND_API_KEY=...` (required when `EMAIL_PROVIDER=resend`)
- `REDIS_URL=redis://...` (required for distributed rate limiting)
- `BACKGROUND_JOBS_MODE=inline|queued`

### Worker process

Run a dedicated API worker process in production when using queued jobs:

```bash
python services/api/scripts/run_worker.py --worker-id railway-worker-1 --interval-seconds 2
```

For operator-triggered one-shot execution from the API service:

```bash
curl -X POST "$API_URL/ops/jobs/run" -H "Content-Type: application/json" -d '{"worker_id":"ops","limit":25}'
```

Run the dedicated threat monitoring worker process in production:

```bash
python -m services.api.app.run_monitoring_worker --worker-name railway-monitoring-worker --interval-seconds 15 --limit 50
```

One-shot diagnostic cycle (safe to run manually in Railway shell):

```bash
python -m services.api.app.run_monitoring_worker --once --worker-name railway-monitoring-worker --limit 50
```

### Honest remaining gaps before true GA / enterprise claims

- Frontend MFA enrollment/challenge UX is not fully wired end-to-end yet.
- Core customer-operable flows are now live in product UI: workspace team admin (member role changes/removal/invites/revoke/resend), seat visibility, no-billing pilot billing-state UX, webhook management (create/edit/enable/rotate/deliveries), and findings decisions/actions workflow.
- Formal SOC 2 control evidence, key rotation automation, and full incident-response runbooks are still required for enterprise procurement.

## Self-serve onboarding wizard (this pass)

The product now includes a persisted, resumable onboarding checklist for each workspace:

- API endpoints: `GET /onboarding/state`, `PATCH /onboarding/state`.
- Database-backed state table: `workspace_onboarding_states` (migration `0010_onboarding_state.sql`).
- Checklist merges **automatic milestones** (asset added, integration connected, invited teammates, analysis run) with **manual setup confirmations** (industry profile and policy baseline).
- UI entry points: `/onboarding`, dashboard onboarding panel progress, and in-app `/help` start-here guide.

This closes a core founder-led gap by making first-run setup resumable and visible to the customer from within the product.

## Workspace operations workflow (live mode)

Recent SaaS workflow upgrades now prioritize real customer records over scenario-only data in authenticated routes:

- Workspace-scoped **Assets CRUD**: `GET/POST /assets`, `GET/PATCH/DELETE /assets/{id}`.
- Workspace-scoped **Targets CRUD**: `GET/POST /targets`, `GET/PATCH/DELETE /targets/{id}`.
- Module config persistence with workspace RBAC: `GET/PUT /modules/{threat|compliance|resilience}/config`.
- History retrieval for operator review: `GET /history`, `GET /history/{id}`.
- Export endpoints include findings plus list/detail status tracking: `POST /exports/findings`, `GET /exports`, `GET /exports/{id}`.
- Export downloads now return real generated artifacts at `GET /exports/{id}/download` (CSV/JSON) with files stored under `EXPORTS_DIR` (`/tmp/decoda-exports` by default).
- Alert notifications now queue outbound webhook/email delivery attempts via `background_jobs`; run `python services/api/scripts/run_worker.py` to process queued deliveries.
- Slack alerting is now supported via incoming webhooks (`/integrations/slack`) with delivery logs, test-send, retries, and routing preferences (`/integrations/routing/{channel_type}`).
- Billing foundations remain in place (`BILLING_PROVIDER=none|paddle|stripe`). For pilot launch posture, run `BILLING_PROVIDER=none` so billing is intentionally disabled while auth/workspace/alerts/exports/integrations remain fully operable.
- Team and seat administration endpoints: `PATCH/DELETE /workspace/members/{id}`, `GET /team/seats`.
- Team invitation lifecycle endpoints: `GET/POST /workspace/invitations`, `POST /workspace/invitations/{id}/resend`, `DELETE /workspace/invitations/{id}`, `POST /workspace/invitations/accept`.
- Finding action workflow endpoints: `POST /findings/{id}/decision`, `POST /findings/{id}/actions`, `PATCH /actions/{id}`, `GET /actions`, `GET /decisions`.
- Protected pages now follow an auth-safe client fetch pattern using `usePilotAuth().authHeaders()` for workspace-scoped calls (`alerts`, `integrations`, `templates`, and settings subflows).

Templates remain onboarding-only (`GET /templates`, `POST /templates/{id}/apply`) and do not replace live customer data in authenticated operations.

## Repository Layout

- `apps/web` — Next.js dashboard UI.
- `services/api` — FastAPI gateway for dashboard, risk, and threat aggregation.
- `services/risk-engine` — FastAPI Phase 1 heuristic risk scoring service.
- `services/threat-engine` — FastAPI Feature 2 threat analysis service for contract, transaction, and market anomaly scoring.
- `services/oracle-service` — Oracle worker.
- `services/compliance-service` — Compliance worker.
- `services/reconciliation-service` — Reconciliation worker.
- `services/event-watcher` — Event ingestion worker.
- `phase1_local` — Shared local SQLite helpers.
- `scripts` — Repo-root bootstrap and smoke-check helpers.

## Local Development Quickstart

### macOS / Linux quickstart

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-local.txt
npm install --workspace apps/web
python services/api/scripts/seed.py
python services/risk-engine/scripts/seed.py
python services/threat-engine/scripts/seed.py
python services/compliance-service/scripts/seed.py
python services/reconciliation-service/scripts/seed.py
python scripts/run_service.py risk-engine --reload
python scripts/run_service.py threat-engine --reload
python scripts/run_service.py compliance-service --reload
python scripts/run_service.py reconciliation-service --reload
python scripts/run_service.py api --reload
npm run dev --workspace apps/web
```

Open:

- API docs: `http://localhost:8000/docs`
- Risk-engine docs: `http://localhost:8001/docs`
- Threat-engine docs: `http://localhost:8002/docs`
- Oracle-service docs: `http://localhost:8003/docs`
- Compliance-service docs: `http://localhost:8004/docs`
- Reconciliation-service docs: `http://localhost:8005/docs`
- Dashboard: `http://localhost:3000`

## Windows CMD: exact repo-root commands

These commands are the recommended fresh-clone flow from the repo root in **Windows CMD**. They do not require manually exporting `PYTHONPATH`.

### 1) Create and activate the virtual environment

```cmd
python -m venv .venv
.venv\Scripts\activate
```

### 2) Install backend dependencies

```cmd
python -m pip install --upgrade pip
python -m pip install -r requirements-local.txt
```

### 3) Install frontend dependencies

```cmd
npm install --workspace apps/web
```

### 4) Seed the shared local SQLite state

```cmd
python services\api\scripts\seed.py
python services\risk-engine\scripts\seed.py
python services\threat-engine\scripts\seed.py
python services\oracle-service\scripts\seed.py
python services\compliance-service\scripts\seed.py
python services\reconciliation-service\scripts\seed.py
```

### 5) Open seven Windows CMD terminals from the repo root and run every local service + the web app

#### Terminal 1 — risk-engine

```cmd
.venv\Scripts\activate
python scripts\run_service.py risk-engine --reload
```

#### Terminal 2 — threat-engine

```cmd
.venv\Scripts\activate
python scripts\run_service.py threat-engine --reload
```

#### Terminal 3 — oracle-service

```cmd
.venv\Scripts\activate
python scripts\run_service.py oracle-service --reload
```

#### Terminal 4 — compliance-service

```cmd
.venv\Scripts\activate
python scripts\run_service.py compliance-service --reload
```

#### Terminal 5 — reconciliation-service

```cmd
.venv\Scripts\activate
python scripts\run_service.py reconciliation-service --reload
```

#### Terminal 6 — API gateway

```cmd
.venv\Scripts\activate
python scripts\run_service.py api --reload
```

#### Terminal 7 — Next.js web app

```cmd
.venv\Scripts\activate
npm run dev --workspace apps/web
```

### 6) Local ports used by the repo-root service runner

These are the exact defaults used by `python scripts\run_service.py <service>` from the repo root on Windows CMD.

| Service | Port | URL |
| --- | --- | --- |
| API gateway | `8000` | `http://localhost:8000` |
| risk-engine | `8001` | `http://localhost:8001` |
| threat-engine | `8002` | `http://localhost:8002` |
| oracle-service | `8003` | `http://localhost:8003` |
| compliance-service | `8004` | `http://localhost:8004` |
| reconciliation-service | `8005` | `http://localhost:8005` |
| web app | `3000` | `http://localhost:3000` |

### 7) Open the local apps

- API docs: `http://localhost:8000/docs`
- Risk-engine docs: `http://localhost:8001/docs`
- Threat-engine docs: `http://localhost:8002/docs`
- Oracle-service docs: `http://localhost:8003/docs`
- Compliance-service docs: `http://localhost:8004/docs`
- Reconciliation-service docs: `http://localhost:8005/docs`
- Dashboard: `http://localhost:3000`

## Local backend behavior

- `services/api` defaults to `RISK_ENGINE_URL=http://localhost:8001`, `THREAT_ENGINE_URL=http://localhost:8002`, `COMPLIANCE_SERVICE_URL=http://localhost:8004`, and `RECONCILIATION_SERVICE_URL=http://localhost:8005`.
- `services/api /risk/dashboard` prefers live risk-engine evaluations.
- `services/api /threat/dashboard` prefers live threat-engine data for Feature 2 cards, alerts, and detections.
- `services/api /compliance/dashboard` prefers live compliance-service data for Feature 3 transfer wrappers, residency controls, policy state, and governance ledger panels.
- `services/api /resilience/dashboard` prefers live reconciliation-service data for Feature 4 reconciliation, backstop controls, and local incident ledger panels.
- If any backend is unavailable or times out, the API returns explicit fallback-safe dashboard data instead of failing the UI.
- The dashboard renders both live and degraded states without blanking the page.
- Browser demo interactions call the API gateway, not the backend workers directly, so the Windows CMD workflow remains repo-root friendly.

## Demo walkthroughs for Features 2, 3, and 4

These walkthroughs assume you started the services with the exact Windows CMD commands above and that the dashboard is running at `http://localhost:3000`.

### Feature 2 demo walkthrough — threat analysis

1. Open `http://localhost:3000` and scroll to **Feature 2 · Preemptive Cybersecurity & AI Threat Defense**.
2. Confirm the runtime banner says the API and backend services are online.
3. In **Feature 2 demo interactions**, click **Flash-loan-like tx** and confirm the result returns a high score with a `block` recommendation.
4. Click **Admin abuse tx** to inspect the privilege-abuse path and compare the explanation + matched reasons.
5. Click **Normal market** or **Spoofing-like market** to see the market anomaly flow exercise `POST /threat/analyze/market`.
6. Review **Active alerts** and **Recent detections** to confirm the dashboard cards match the current threat-engine feed.

### Feature 3 demo walkthrough — compliance wrappers and governance

1. Scroll to **Feature 3 · Sovereign-Grade Compliance & Governance**.
2. In **Compliance Operations**, leave **Compliant transfer approved** selected and click **Run transfer screening**.
3. Switch to **Blocked by sanctions** or **Review for incomplete KYC** and run the transfer screening again to compare deterministic wrapper reasons.
4. In the residency section, run **Denied restricted region** to verify the geopatriation controls and routing recommendation.
5. In the governance section, submit **Freeze wallet**, **Pause asset**, or **Allowlist wallet**.
6. After the page refreshes, verify the new governance record appears under **Latest governance actions** and that **Asset transfer status** / policy state cards reflect the updated state.

### Feature 4 demo walkthrough — reconciliation, backstops, and incidents

1. Scroll to **Feature 4 · Interoperability & Systemic Resilience**.
2. In **Resilience Operations**, run **Critical divergence** to compare expected supply vs. observed multi-ledger supply.
3. Run **Pause bridge** in the backstop section to exercise the deterministic restricted / paused safeguards.
4. Run **Reconciliation failure** or **Market circuit breaker** in the incident section to append a new resilience incident record.
5. Confirm the response payloads render in the demo panel and then review **Ledger assessments**, **Backstop decision**, and **Latest incident records** for the matching dashboard state.
6. Optionally refresh the page once more to verify the latest incident list remains visible from the local ledger.

## Feature 2 smoke / regression tests

### What the smoke suite covers

- Backend pytest smoke checks for `GET /threat/dashboard`, `POST /threat/analyze/transaction`, `POST /threat/analyze/market`, and `POST /threat/analyze/contract`.
- Stable response-shape assertions for `score`, `severity`, `matched_patterns`, `recommended_action`, and dashboard anomaly metadata.
- Explicit fallback-shape verification when the threat-engine is unavailable.
- Frontend Playwright smoke coverage for `http://localhost:3000`, the Feature 2 dashboard section, and a no-blank-page / no-fatal-crash sanity check.

### Install the smoke-test dependencies once

#### Python

```cmd
python -m pip install -r requirements-local.txt
```

#### Playwright package + browser

```cmd
npm install
npx playwright install
```

### Windows CMD: exact smoke-test commands from the repo root

#### Backend Feature 2 smoke tests

```cmd
python -m pytest services\api\tests\test_feature2_smoke.py -q
```

#### Repo-root pytest run

```cmd
pytest -q
```

This repo-root pytest command is now scoped through `pytest.ini` so the service test suites run cleanly from the monorepo root without cross-importing the wrong `app` package.

#### Frontend Feature 2 smoke test

Start the frontend first in another terminal:

```cmd
npm run dev --workspace apps/web
```

Then run:

```cmd
npx playwright test apps\web\tests\feature2-smoke.spec.ts
```

#### Full smoke suite in one repo-root command

Start the frontend first in another terminal, then run:

```cmd
python scripts\smoke_feature2.py
```

Or use the npm wrapper:

```cmd
npm run smoke:feature2
```

`scripts\smoke_feature2.py` now waits longer for `http://127.0.0.1:3000`, retries before failing, and reports whether the Next.js app appears to still be compiling versus not running at all.
It now uses a two-step frontend readiness check: a lightweight `GET /api/health` probe confirms that the Next.js dev server is alive first, then the script retries the homepage with a longer per-request timeout so slow cold-start compiles do not produce false "frontend unavailable" failures.

### Which services must be running first?

- `python -m pytest services\api\tests\test_feature2_smoke.py -q` — **no services required**. The backend smoke checks use FastAPI's in-process test client and stub the threat-engine availability states.
- `npx playwright test apps\web\tests\feature2-smoke.spec.ts` — **requires the Next.js frontend on `http://localhost:3000`**. The page can still render fallback content if the API gateway or threat-engine are offline.
- `npm run smoke:feature2` — **requires the Next.js frontend on `http://localhost:3000`** because the Python runner executes backend pytest first and then the Playwright smoke test.

## Feature 2: threat-engine endpoints

### Threat-engine direct endpoints

- `GET http://localhost:8002/health`
- `GET http://localhost:8002/state`
- `GET http://localhost:8002/scenarios`
- `GET http://localhost:8002/scenarios/{scenario_name}`
- `POST http://localhost:8002/analyze/contract`
- `POST http://localhost:8002/analyze/transaction`
- `POST http://localhost:8002/analyze/market`
- `GET http://localhost:8002/dashboard`

### API gateway Feature 2 endpoints

- `GET http://localhost:8000/threat/dashboard`
- `POST http://localhost:8000/threat/analyze/contract`
- `POST http://localhost:8000/threat/analyze/transaction`
- `POST http://localhost:8000/threat/analyze/market`

## Example curl requests for Feature 2

### 1) Threat dashboard

```cmd
curl http://localhost:8000/threat/dashboard
```

### 2) Contract analysis

```cmd
curl -X POST http://localhost:8000/threat/analyze/contract ^
  -H "Content-Type: application/json" ^
  --data @services\threat-engine\data\sample_contract.json
```

### 3) Safe transaction analysis

```cmd
curl -X POST http://localhost:8000/threat/analyze/transaction ^
  -H "Content-Type: application/json" ^
  --data @services\threat-engine\data\safe_transaction.json
```

### 4) Suspicious flash-loan-like transaction analysis

```cmd
curl -X POST http://localhost:8000/threat/analyze/transaction ^
  -H "Content-Type: application/json" ^
  --data @services\threat-engine\data\flash_loan_transaction.json
```

### 5) Admin privilege abuse transaction analysis

```cmd
curl -X POST http://localhost:8000/threat/analyze/transaction ^
  -H "Content-Type: application/json" ^
  --data @services\threat-engine\data\admin_privilege_abuse.json
```

### 6) Normal market behavior analysis

```cmd
curl -X POST http://localhost:8000/threat/analyze/market ^
  -H "Content-Type: application/json" ^
  --data @services\threat-engine\data\normal_market_behavior.json
```

### 7) Spoofing-like market behavior analysis

```cmd
curl -X POST http://localhost:8000/threat/analyze/market ^
  -H "Content-Type: application/json" ^
  --data @services\threat-engine\data\spoofing_market_behavior.json
```

### 8) Wash-trading-like market behavior analysis

```cmd
curl -X POST http://localhost:8000/threat/analyze/market ^
  -H "Content-Type: application/json" ^
  --data @services\threat-engine\data\wash_trading_market_behavior.json
```

## Sample request bodies

### `POST /threat/analyze/contract`

```json
{
  "contract_name": "ProxyTreasuryRouter",
  "address": "0xdddd000000000000000000000000000000router",
  "verified_source": false,
  "audit_count": 0,
  "created_days_ago": 4,
  "admin_roles": ["governance-multisig"],
  "calling_actor": "ops-hot-wallet",
  "function_summaries": [
    {
      "name": "flashLoan",
      "summary": "Borrows assets atomically before external swaps.",
      "risk_flags": ["flash-loan-indicator"]
    },
    {
      "name": "setImplementation",
      "summary": "Updates proxy implementation address.",
      "risk_flags": ["privileged-admin"]
    },
    {
      "name": "sweepFunds",
      "summary": "Moves full balance to a receiver wallet.",
      "risk_flags": ["drain-path"]
    }
  ],
  "findings": [
    "delegatecall present in proxy execution path",
    "external call to untrusted router",
    "same-flow borrow / swap / repay sequence observed"
  ],
  "flags": {
    "delegatecall": true,
    "untrusted_external_call": true,
    "unsafe_admin_action": true,
    "high_value_drain_path": true,
    "burst_risk_actions": true
  }
}
```

### `POST /threat/analyze/transaction` safe transaction

```json
{
  "wallet": "0xaaaa00000000000000000000000000000000safe",
  "actor": "treasury-ops",
  "action_type": "settlement",
  "protocol": "TreasurySettlement",
  "amount": 125000.0,
  "asset": "USTB",
  "call_sequence": ["validateInvoice", "settleTreasuryTransfer"],
  "flags": {
    "contains_flash_loan": false,
    "unexpected_admin_call": false,
    "untrusted_contract": false,
    "rapid_drain_indicator": false
  },
  "counterparty_reputation": 91,
  "actor_role": "treasury-operator",
  "expected_actor_roles": ["treasury-operator", "finance-controller"],
  "burst_actions_last_5m": 1
}
```

### `POST /threat/analyze/transaction` suspicious flash-loan-like transaction

```json
{
  "wallet": "0xbbbb0000000000000000000000000000000flash",
  "actor": "unknown-bot-17",
  "action_type": "rebalance",
  "protocol": "LiquidityRouter",
  "amount": 2400000.0,
  "asset": "USTB",
  "call_sequence": ["borrow", "swap", "swap", "swap", "repay"],
  "flags": {
    "contains_flash_loan": true,
    "unexpected_admin_call": false,
    "untrusted_contract": true,
    "rapid_drain_indicator": true
  },
  "counterparty_reputation": 24,
  "actor_role": "external-bot",
  "expected_actor_roles": ["treasury-operator"],
  "burst_actions_last_5m": 5
}
```

### `POST /threat/analyze/transaction` admin privilege abuse scenario

```json
{
  "wallet": "0xcccc0000000000000000000000000000000admin",
  "actor": "ops-hot-wallet",
  "action_type": "admin",
  "protocol": "ProxyTreasuryVault",
  "amount": 650000.0,
  "asset": "USTB",
  "call_sequence": ["pauseVault", "setImplementation", "sweepFunds"],
  "flags": {
    "contains_flash_loan": false,
    "unexpected_admin_call": true,
    "untrusted_contract": false,
    "rapid_drain_indicator": true
  },
  "counterparty_reputation": 41,
  "actor_role": "ops-hot-wallet",
  "expected_actor_roles": ["governance-multisig"],
  "burst_actions_last_5m": 4
}
```

### `POST /threat/analyze/market` normal market behavior

```json
{
  "asset": "USTB",
  "venue": "synthetic-exchange",
  "timeframe_minutes": 15,
  "current_volume": 1350000.0,
  "baseline_volume": 1180000.0,
  "participant_diversity": 18,
  "dominant_cluster_share": 0.18,
  "order_flow_summary": {
    "large_orders": 3,
    "rapid_cancellations": 1,
    "rapid_swings": 1,
    "circular_trade_loops": 0,
    "self_trade_markers": 0
  },
  "candles": [
    {"timestamp": "2026-03-18T09:00:00Z", "open": 1.0, "high": 1.002, "low": 0.999, "close": 1.001, "volume": 420000},
    {"timestamp": "2026-03-18T09:05:00Z", "open": 1.001, "high": 1.003, "low": 1.0, "close": 1.002, "volume": 450000},
    {"timestamp": "2026-03-18T09:10:00Z", "open": 1.002, "high": 1.004, "low": 1.001, "close": 1.003, "volume": 480000}
  ],
  "wallet_activity": [
    {"cluster_id": "treasury-desk-a", "trade_count": 5, "net_volume": 240000},
    {"cluster_id": "custodian-flow", "trade_count": 4, "net_volume": 210000},
    {"cluster_id": "market-maker-1", "trade_count": 6, "net_volume": 320000}
  ]
}
```

### `POST /threat/analyze/market` spoofing-like market behavior

Use `services\threat-engine\data\spoofing_market_behavior.json`.

### `POST /threat/analyze/market` wash-trading-like market behavior

Use `services\threat-engine\data\wash_trading_market_behavior.json`.

## Demo flow for Feature 2

1. Start `risk-engine`, `threat-engine`, `api`, and `web` from the repo root using the commands above.
2. Open `http://localhost:3000`.
3. In the new **Feature 2 · Preemptive Cybersecurity & AI Threat Defense** section, review the threat score cards, active alerts, market anomaly summary, and recent detections.
4. Use the in-browser demo panel to trigger safe transaction, flash-loan-like transaction, admin privilege abuse, normal market, spoofing-like market, wash-trading-like market, or contract scan analyses.
5. Compare the allow / review / block results with the reasons list to see which deterministic rules fired.
6. Stop `threat-engine` and refresh the dashboard to verify that the API gateway and UI degrade gracefully to fallback Feature 2 data.

## Validation / smoke checks

### Python service tests

```cmd
python -m unittest services.risk-engine.tests.test_risk_engine
python -m unittest services.threat-engine.tests.test_threat_engine
python -m unittest services.api.tests.test_risk_dashboard
```

### Frontend build validation

```cmd
npm run build --workspace apps/web
```

### Repo smoke check

```cmd
python scripts\smoke_phase1.py
```

The smoke checks prove that:

- the risk-engine starts,
- the threat-engine starts,
- the API starts,
- the API fetches live risk-engine and threat-engine data when available, and
- the API returns frontend-safe fallback shapes when the backends become unavailable.

## Final local verification checklist

From the repo root on Windows CMD, use these exact commands to verify the polish pass locally after starting the services and web app:

```cmd
python -m pytest services\api\tests\test_feature2_smoke.py -q
python -m pytest services\api\tests\test_feature3_smoke.py -q
python -m pytest services\api\tests\test_feature4_smoke.py -q
python -m pytest services\api\tests\test_risk_dashboard.py -q
npx playwright test apps\web\tests\feature2-smoke.spec.ts
npx playwright test apps\web\tests\feature3-smoke.spec.ts
npx playwright test apps\web\tests\feature4-smoke.spec.ts
```

Files changed in this polish pass:

- `README.md`
- `services/api/app/main.py`
- `apps/web/app/page.tsx`

## Optional Docker support

Docker remains available as an optional workflow through `docker-compose.yml`, but it is no longer the primary local path.


## Feature 3: sovereign-grade compliance & governance

### Local ports

- API gateway: `http://localhost:8000`
- Risk-engine: `http://localhost:8001`
- Threat-engine: `http://localhost:8002`
- Oracle-service: `http://localhost:8003`
- Compliance-service: `http://localhost:8004`
- Web dashboard: `http://localhost:3000`

### Windows CMD: exact Feature 3 run commands from the repo root

```cmd
.venv\Scripts\activate
python services\compliance-service\scripts\seed.py
python services\reconciliation-service\scripts\seed.py
python scripts\run_service.py compliance-service --reload
```

In separate terminals from the repo root:

```cmd
.venv\Scripts\activate
python scripts\run_service.py api --reload
```

```cmd
.venv\Scripts\activate
npm run dev --workspace apps/web
```

### compliance-service direct endpoints

- `GET http://localhost:8004/health`
- `GET http://localhost:8004/dashboard`
- `POST http://localhost:8004/screen/transfer`
- `POST http://localhost:8004/screen/residency`
- `GET http://localhost:8004/policy/state`
- `GET http://localhost:8004/governance/actions`
- `GET http://localhost:8004/governance/actions/{action_id}`
- `POST http://localhost:8004/governance/actions`
- `GET http://localhost:8004/scenarios`
- `GET http://localhost:8004/scenarios/{scenario_name}`

### API gateway Feature 3 endpoints

- `GET http://localhost:8000/compliance/dashboard`
- `POST http://localhost:8000/compliance/screen/transfer`
- `POST http://localhost:8000/compliance/screen/residency`
- `GET http://localhost:8000/compliance/policy/state`
- `GET http://localhost:8000/compliance/governance/actions`
- `GET http://localhost:8000/compliance/governance/actions/{action_id}`
- `POST http://localhost:8000/compliance/governance/actions`

### Sample curl requests for Feature 3

#### 1) Dashboard

```cmd
curl http://localhost:8000/compliance/dashboard
```

#### 2) Compliant transfer approved

```cmd
curl -X POST http://localhost:8000/compliance/screen/transfer ^
  -H "Content-Type: application/json" ^
  --data @services\compliance-service\data\compliant_transfer_approved.json
```

#### 3) Blocked transfer due to sanctions flag

```cmd
curl -X POST http://localhost:8000/compliance/screen/transfer ^
  -H "Content-Type: application/json" ^
  --data @services\compliance-service\data\blocked_transfer_sanctions.json
```

#### 4) Blocked transfer due to blocklisted wallet

```cmd
curl -X POST http://localhost:8000/compliance/screen/transfer ^
  -H "Content-Type: application/json" ^
  --data @services\compliance-service\data\blocked_transfer_blocklist.json
```

#### 5) Review due to incomplete KYC

```cmd
curl -X POST http://localhost:8000/compliance/screen/transfer ^
  -H "Content-Type: application/json" ^
  --data @services\compliance-service\data\review_transfer_incomplete_kyc.json
```

#### 6) Review due to restricted jurisdiction

```cmd
curl -X POST http://localhost:8000/compliance/screen/transfer ^
  -H "Content-Type: application/json" ^
  --data @services\compliance-service\data\review_transfer_restricted_jurisdiction.json
```

#### 7) Denied residency request for restricted region

```cmd
curl -X POST http://localhost:8000/compliance/screen/residency ^
  -H "Content-Type: application/json" ^
  --data @services\compliance-service\data\denied_residency_restricted_region.json
```

#### 8) Freeze wallet governance action

```cmd
curl -X POST http://localhost:8000/compliance/governance/actions ^
  -H "Content-Type: application/json" ^
  --data @services\compliance-service\data\governance_freeze_wallet.json
```

#### 9) Pause asset governance action

```cmd
curl -X POST http://localhost:8000/compliance/governance/actions ^
  -H "Content-Type: application/json" ^
  --data @services\compliance-service\data\governance_pause_asset.json
```

#### 10) Allowlist wallet governance action

```cmd
curl -X POST http://localhost:8000/compliance/governance/actions ^
  -H "Content-Type: application/json" ^
  --data @services\compliance-service\data\governance_allowlist_wallet.json
```

#### 11) Transfer blocked because asset is paused

```cmd
curl -X POST http://localhost:8000/compliance/screen/transfer ^
  -H "Content-Type: application/json" ^
  --data @services\compliance-service\data\transfer_blocked_asset_paused.json
```

### Expected demo flow for Feature 3

1. Start `risk-engine`, `threat-engine`, `compliance-service`, `api`, and `apps/web` from the repo root.
2. Open `http://localhost:3000` and scroll to **Feature 3 · Sovereign-Grade Compliance & Governance**.
3. Review the transfer wrapper cards, residency decision, policy-state counts, and latest governance actions.
4. Use the Feature 3 demo panel to run a transfer screening, run a residency screening, and submit a governance action.
5. Refresh the page or re-open `GET /compliance/dashboard` to verify that governance actions update policy state and asset transfer status.
6. Stop `compliance-service` and retry the same dashboard/API flows to confirm the API gateway and UI degrade gracefully with explicit fallback data instead of blank states.

### Graceful fallback behavior

- If `compliance-service` is unavailable, the API gateway returns deterministic fallback Feature 3 payloads for the dashboard, transfer screening, residency screening, and governance actions.
- The dashboard renders fallback cards, governance actions, and demo interaction responses without a blank page.
- Feature 1 and Feature 2 routes continue to operate independently when Feature 3 is offline.

## Feature 4: interoperability & systemic resilience

Feature 4 extends the existing `services/reconciliation-service` instead of introducing a new service. The local vertical slice remains deterministic, explainable, and demoable without any real blockchain, bridge, exchange, or third-party network dependency.

### Current local ports

- API gateway: `http://localhost:8000`
- Risk-engine: `http://localhost:8001`
- Threat-engine: `http://localhost:8002`
- Oracle-service: `http://localhost:8003`
- Compliance-service: `http://localhost:8004`
- Reconciliation-service: `http://localhost:8005`
- Web dashboard: `http://localhost:3000`

### Windows CMD: exact Feature 4 run commands from the repo root

```cmd
.venv\Scripts\activate
python services\reconciliation-service\scripts\seed.py
python scripts\run_service.py reconciliation-service --reload
```

```cmd
.venv\Scripts\activate
python scripts\run_service.py api --reload
```

```cmd
.venv\Scripts\activate
npm run dev --workspace apps/web
```

### Reconciliation-service direct endpoints

- `GET http://localhost:8005/health`
- `GET http://localhost:8005/state`
- `GET http://localhost:8005/dashboard`
- `POST http://localhost:8005/reconcile/state`
- `POST http://localhost:8005/backstop/evaluate`
- `POST http://localhost:8005/incidents/record`
- `GET http://localhost:8005/incidents`
- `GET http://localhost:8005/incidents/{event_id}`
- `GET http://localhost:8005/scenarios`
- `GET http://localhost:8005/scenarios/{scenario_name}`

### API gateway Feature 4 endpoints

- `GET http://localhost:8000/resilience/dashboard`
- `POST http://localhost:8000/resilience/reconcile/state`
- `POST http://localhost:8000/resilience/backstop/evaluate`
- `POST http://localhost:8000/resilience/incidents/record`
- `GET http://localhost:8000/resilience/incidents`
- `GET http://localhost:8000/resilience/incidents/{event_id}`

### Sample curl requests for Feature 4

#### 1) Resilience dashboard

```cmd
curl http://localhost:8000/resilience/dashboard
```

#### 2) Cross-chain reconciliation

```cmd
curl -X POST http://localhost:8000/resilience/reconcile/state ^
  -H "Content-Type: application/json" ^
  -d "{""asset_id"":""USTB-2026"",""expected_total_supply"":1000000,""ledgers"":[{""ledger_name"":""ethereum"",""reported_supply"":740000,""locked_supply"":10000,""pending_settlement"":45000,""last_updated_at"":""2026-03-18T11:40:00Z"",""transfer_count"":125,""reconciliation_weight"":1.0},{""ledger_name"":""avalanche"",""reported_supply"":510000,""locked_supply"":5000,""pending_settlement"":38000,""last_updated_at"":""2026-03-18T11:42:00Z"",""transfer_count"":118,""reconciliation_weight"":1.0},{""ledger_name"":""private-bank-ledger"",""reported_supply"":210000,""locked_supply"":0,""pending_settlement"":12000,""last_updated_at"":""2026-03-18T09:10:00Z"",""transfer_count"":21,""reconciliation_weight"":1.0}]}"
```

#### 3) Backstop evaluation

```cmd
curl -X POST http://localhost:8000/resilience/backstop/evaluate ^
  -H "Content-Type: application/json" ^
  -d "{""asset_id"":""USTB-2026"",""volatility_score"":71,""cyber_alert_score"":89,""reconciliation_severity"":81,""oracle_confidence_score"":36,""compliance_incident_score"":74,""current_market_mode"":""restricted""}"
```

#### 4) Record an incident

```cmd
curl -X POST http://localhost:8000/resilience/incidents/record ^
  -H "Content-Type: application/json" ^
  -d "{""event_type"":""reconciliation-failure"",""trigger_source"":""reconciliation-engine"",""related_asset_id"":""USTB-2026"",""affected_assets"":[""USTB-2026""],""affected_ledgers"":[""ethereum"",""avalanche"",""private-bank-ledger""],""severity"":""critical"",""status"":""open"",""summary"":""Critical multi-ledger divergence with duplicate mint risk triggered a bridge pause."",""metadata"":{""scenario"":""critical-supply-divergence-double-count-risk"",""ticket"":""RES-4001""}}"
```

### Expected demo flow for Feature 4

1. Start `risk-engine`, `threat-engine`, `compliance-service`, `reconciliation-service`, `api`, and `web` from the repo root.
2. Open `http://localhost:3000` and scroll to **Feature 4 · Interoperability & Systemic Resilience**.
3. Review the reconciliation cards for mismatch amount, severity, stale ledger count, and current backstop decision.
4. Use the Feature 4 demo panel to run a reconciliation scenario, run a backstop evaluation, and record an incident.
5. Open `http://localhost:8005/docs` or `http://localhost:8000/docs` to replay the same scenarios with the OpenAPI examples.
6. Stop `reconciliation-service` and refresh the dashboard or retry the same API requests to confirm the API gateway and UI degrade gracefully to explicit fallback resilience data.

### Graceful fallback behavior

- If `reconciliation-service` is unavailable, the API gateway returns deterministic fallback Feature 4 payloads for the dashboard, reconciliation, backstop evaluation, and incident creation flows.
- The dashboard still renders reconciliation cards, safeguards, and recent incident records without blank sections.
- Features 2 and 3 continue to operate independently when Feature 4 is offline.

### Feature 4 smoke / regression commands

```cmd
python -m pytest services\reconciliation-service\tests\test_reconciliation_service.py -q
python -m pytest services\api\tests\test_feature4_smoke.py -q
python scripts\smoke_feature4.py
```

## Customer deployment

This repo now ships with a **split experience**:

- `/` is a public marketing homepage for Decoda RWA Guard.
- `/security` is the public trust/security page.
- `/dashboard`, `/threat`, `/compliance`, `/resilience`, `/history`, `/settings`, and `/settings/security` are the authenticated product routes.
- The product always prefers live data first, but it preserves the existing deterministic fallback and sample-safe behavior so the UI never blanks when a dependency is unavailable.

### Railway requirements (API)

- **Build context must remain the repo root.** Railway must build from the repository root so `services/api/Dockerfile` can copy the sibling fixture and service folders used by graceful fallback mode.
- **Deploy target remains `services/api/Dockerfile`.**
- Required Railway environment variables:
  - `LIVE_MODE_ENABLED=true` for persisted workspace records.
  - `DATABASE_URL=postgresql://...` using the Neon connection string and `sslmode=require`.
  - `AUTH_TOKEN_SECRET=<long-random-secret>`.
  - `CORS_ALLOWED_ORIGINS=http://localhost:3000,https://<your-vercel-app>.vercel.app`.
  - `RISK_ENGINE_URL`, `THREAT_ENGINE_URL`, `COMPLIANCE_SERVICE_URL`, and `RECONCILIATION_SERVICE_URL` for the existing downstream services.
  - Any existing mode/build variables already used by `services/api` should stay in place.

### Vercel requirements (web)

- Vercel should continue deploying the `apps/web` project.
- The Vercel **Root Directory** should remain `apps/web` for this monorepo.
- Required Vercel environment variables by scope:
  - **Production:** `NEXT_PUBLIC_LIVE_MODE_ENABLED=true`, `API_URL=https://<your-railway-api>.up.railway.app` (preferred) or `NEXT_PUBLIC_API_URL=https://<your-railway-api>.up.railway.app`, and `NEXT_PUBLIC_API_TIMEOUT_MS=5000`.
  - **Preview:** `API_URL=https://<preview-or-shared-railway-api>.up.railway.app` (preferred) or `NEXT_PUBLIC_API_URL=https://<preview-or-shared-railway-api>.up.railway.app`, `NEXT_PUBLIC_API_TIMEOUT_MS=5000`, and `NEXT_PUBLIC_LIVE_MODE_ENABLED=true` is strongly recommended so operators can see the intended mode directly in build logs and runtime diagnostics.
  - **Development:** `NEXT_PUBLIC_LIVE_MODE_ENABLED=false` (or `true` when testing pilot mode locally), `NEXT_PUBLIC_API_URL=http://127.0.0.1:8000`, and optional `NEXT_PUBLIC_API_TIMEOUT_MS=5000`.
- Production builds fail clearly when `NEXT_PUBLIC_LIVE_MODE_ENABLED` is missing/invalid or when both API URL variables are absent. Preview builds now warn for missing `NEXT_PUBLIC_LIVE_MODE_ENABLED`, prefer server-side `API_URL`, and only fail when the missing API settings would leave the auth proxy unusable before runtime.
- Operators can inspect `/api/build-info` to see the deployment environment, branch, commit SHA, and runtime-config summary before debugging the backend.
- The web app uses the API URL to resolve **live**, **live (degraded)**, **fallback**, and **sample** states in the UI.

### Neon / Postgres expectations

- `DATABASE_URL` must point at the Neon Postgres database used for live pilot persistence.
- The connection string should include SSL requirements expected by Neon, typically `sslmode=require`.
- Live workspace history writes to the existing pilot tables: `users`, `workspaces`, `workspace_members`, `analysis_runs`, `alerts`, `governance_actions`, `incidents`, and `audit_logs`.

### Migration and seed flow

Run these commands from the repo root after setting the Railway/Neon API environment variables locally or in a one-off deploy shell:

```bash
python services/api/scripts/migrate.py
python services/api/scripts/seed.py --pilot-demo
```

- `migrate.py` applies the live pilot schema to Neon.
- `seed.py --pilot-demo` creates a demo workspace/user for customer walkthroughs while preserving the existing local demo workflow.
- `AUTH_EXPOSE_DEBUG_TOKENS=true` can be used in local/dev environments to return verification/reset tokens in API JSON for manual flow testing. Keep this disabled in shared/prod environments.

### Verifying live vs degraded vs fallback vs sample

Use `GET /health/details` together with the dashboard UI to verify the deployment mode:

1. **Live**: `/health/details` is reachable, the gateway is healthy, and product badges read `Live`.
2. **Live (degraded)**: the gateway is reachable but one or more feature feeds explain that fallback coverage is active.
3. **Fallback**: a feature badge reads `Fallback`, usually because a downstream dependency timed out or returned an error while the UI stayed populated.
4. **Sample**: no live API is configured for the web app, so deterministic sample-safe payloads render across the experience.

You can also inspect `/health/details` to confirm dependency diagnostics, build/runtime markers, and the current fallback-safe fixture resolution.

### Deployment checklist

- [ ] Railway build context is the **repo root**.
- [ ] Railway deploys `services/api/Dockerfile`.
- [ ] `DATABASE_URL` points to Neon with SSL enabled.
- [ ] `AUTH_TOKEN_SECRET` is set.
- [ ] All downstream service URLs are configured.
- [ ] Vercel Production sets `NEXT_PUBLIC_LIVE_MODE_ENABLED`, at least one of `API_URL` / `NEXT_PUBLIC_API_URL`, and `NEXT_PUBLIC_API_TIMEOUT_MS`.
- [ ] Vercel Preview sets at least one of `API_URL` / `NEXT_PUBLIC_API_URL`; add `NEXT_PUBLIC_LIVE_MODE_ENABLED` as well so operators do not have to infer whether the deployment should run in demo or live mode.
- [ ] Vercel Root Directory is `apps/web`.
- [ ] `/api/build-info` reports the expected `vercelEnv`, branch, commit SHA, and runtime config summary on the deployed site.
- [ ] `python services/api/scripts/migrate.py` has been run against Neon.
- [ ] Optional pilot seed completed with `python services/api/scripts/seed.py --pilot-demo`.
- [ ] `/health/details` confirms expected dependency and runtime mode.
- [ ] `/` shows the marketing homepage and `/dashboard` opens the authenticated product experience.

### Secret hygiene after any suspected exposure

- Rotate `AUTH_TOKEN_SECRET` in Railway and redeploy the API so all newly issued auth tokens use the new secret.
- Rotate `DATABASE_URL` credentials in Neon/Railway if they were ever shared in logs or screenshots.
- Rotate Vercel + Railway API URL credentials if they include embedded credentials.
- Verify `GET /health` and dashboard APIs only return masked database configuration (`[configured]`) instead of raw DSNs before inviting external evaluators.


## Public self-serve SaaS additions (this pass)

- Billing foundations added in code + schema (`billing_customers`, `billing_subscriptions`, `billing_events`, `plan_entitlements`) with endpoints for plan listing, checkout session, portal session, subscription lookup, and Stripe webhook ingestion.
- Workspace team-management foundations added with invitation create/accept flows and workspace member listing.
- Workspace webhook-management foundations added with create/update/list/rotate-secret plus delivery listing.
- Dashboard fallback behavior is now strict in production/authenticated usage: demo payloads are only allowed when `ENABLE_DEMO_FALLBACKS=true` in non-production.

### New environment variables

- `ENABLE_DEMO_FALLBACKS=true|false` (non-production only; enables sample dashboard payloads)
- `BILLING_PROVIDER=none|paddle|stripe` (defaults to `paddle`; use `none` for pre-launch no-billing mode)
- `PADDLE_API_KEY=...` (required only when Paddle billing is enabled)
- `PADDLE_WEBHOOK_SECRET=...` (required only when Paddle billing is enabled)
- `PADDLE_ENVIRONMENT=sandbox|live`
- `PADDLE_PRICE_ID_PRO=...`, `PADDLE_PRICE_ID_TEAM=...` (and any other public plan keys)
- Optional for frontend overlay checkout: `PADDLE_CLIENT_TOKEN=...`
- `APP_URL=https://your-product-domain` (recommended for Slack alert deep links back into `/alerts`)

### Manual setup required

1. Run migrations: `python services/api/scripts/migrate.py`.
2. (Optional pre-launch) Set `BILLING_PROVIDER=none` to boot the API and product without billing. Billing endpoints will return structured `billing_unavailable` JSON while core auth/dashboard/workspace flows continue to work.
3. To enable Paddle later, configure products/prices and set one `PADDLE_PRICE_ID_<PLAN_KEY>` env var per plan (example: `PADDLE_PRICE_ID_PRO`).
4. Configure Paddle webhook destination to `POST /billing/webhooks/paddle`, then set `PADDLE_WEBHOOK_SECRET` and `PADDLE_API_KEY`.
5. Optional: keep Stripe as a secondary provider by setting `BILLING_PROVIDER=stripe` and configuring Stripe keys/webhook endpoint.
5. Provision a dedicated worker process for queued jobs on Railway.

### Honest external follow-up still required

- Independent penetration testing.
- SOC 2 evidence collection and audit execution.
- Legal/privacy policy and DPA reviews.
- Incident-response tabletop exercises and documented runbooks.
- Key-rotation and backup/restore drill operations.

## Customer-usable SaaS workflow modules (March 2026)

Authenticated product routes now prioritize **live customer operations** over guided scenario walkthroughs.

### Operational modules

- Threat Monitoring (`/threat`) with workspace target selection plus persisted module config.
- Compliance Controls (`/compliance`) with persisted review/evidence policy config and exports hook.
- Resilience Monitoring (`/resilience`) with saved monitoring thresholds and alert acknowledgement workflow.

### New customer data workflows

- Target Manager (`/targets`) for workspace-scoped targets and monitored actors.
- Alerts Center (`/alerts`) for workspace alert listing and lifecycle actions.
- Integrations (`/integrations`) for outbound webhook management and delivery visibility.
- Exports (`/exports`) for operational report export actions.
- Templates (`/templates`) as onboarding helpers only; templates are separate from live target/module records.

### Backend SaaS additions

- `targets` + `target_tags` workspace data model and CRUD endpoints.
- Workspace `module_configs` endpoints for threat/compliance/resilience policy persistence.
- Alert API endpoints for list/detail/status mutation with `alert_events` audit trail.
- Export job endpoints (`/exports/history`, `/exports/alerts`, `/exports/report`) backed by `export_jobs`.
- Integration webhook endpoints under `/integrations/webhooks/*` with secret rotation and delivery logs.
- Slack integration endpoints under `/integrations/slack*` (list/create/update/delete/test + delivery history) with webhook URLs masked in API responses.
- Alert routing endpoints under `/integrations/routing*` for per-channel severity thresholds and enable/disable controls.

### Slack setup (webhook + bot mode)

1. Open Decoda UI (`/integrations`) and choose Slack mode:
   - **Incoming webhook** for compatibility
   - **Bot token** (`xoxb-...`) for richer `chat.postMessage` delivery (**recommended**)
2. Configure destination channel (`#alerts` or channel ID) and save.
3. Run **Test send** to queue a safe test notification.
4. Start worker processing (`python services/api/scripts/run_worker.py`) so queued jobs are delivered.

Notes:
- Slack OAuth install + callback are supported when `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, and `SLACK_OAUTH_REDIRECT_URI` are configured.
- Slack interactivity endpoints are not implemented in this release.
- Slack payloads always include top-level `text` plus Block Kit sections (fallback/accessibility safe).

### Alert routing behavior

- Routing is workspace-scoped and channel-scoped (`dashboard`, `email`, `webhook`, `slack`).
- Each channel rule supports:
  - `enabled` on/off,
  - `severity_threshold` (`low`/`medium`/`high`/`critical`),
  - optional module include/exclude lists,
  - optional target filters,
  - event type filters (currently `alert.created`).
- Dashboard persistence remains on by default (alerts are still saved); routing controls outbound channel fan-out at alert generation time.

### End-to-end operator loop

The live workflow now supports:

`analyze -> persist finding -> route alert (dashboard/email/webhook/slack) -> assign/escalate/suppress/accept -> create/update action -> export evidence`
- Plan enforcement for target limits, exports availability, and advanced module-config controls.

### New/updated environment expectations

No required new env vars were introduced for this pass beyond existing billing/email/auth vars. Existing billing entitlements now include additional limits (`max_targets`, `exports_enabled`, `alert_retention_days`) via migrations.

## Production readiness and UX polish upgrades (March 2026)

### Guided policy builder UX

Policy configuration for Threat Monitoring, Compliance Controls, and Resilience Monitoring now supports:

- guided fields, toggles, thresholds, and checklists by module
- inline summaries of effective policy behavior
- **Advanced policy configuration (JSON)** as an optional collapsible expert mode
- backend normalization for backwards compatibility with existing stored config keys

### Asset/target management UX

Assets and targets now include polished management workflows:

- search and filtering
- richer create/edit forms for metadata, owner/team, risk, notes, and tags
- enable/disable, duplicate (targets), and archive/delete actions
- clearer empty states and next-step guidance

### Slack integration modes

Slack now supports two workspace modes:

1. **Incoming webhook** (legacy-compatible)
2. **Bot token / chat.postMessage** (**recommended** for production)

Slack OAuth self-serve install is supported when SLACK_CLIENT_ID, SLACK_CLIENT_SECRET, and SLACK_OAUTH_REDIRECT_URI are configured. Manual webhook/bot token setup remains available as fallback.

### Integration health diagnostics

New admin-only diagnostics endpoints:

- `GET /system/integrations/health`
- `POST /system/integrations/test-email`
- `POST /system/integrations/test-slack`

These surface actionable readiness checks for Stripe, email, and Slack without exposing secret values.

### New/updated env vars

- `BILLING_PROVIDER=none|paddle|stripe` (`none` disables billing cleanly for pre-launch)
- `STRICT_PRODUCTION_BILLING=true` (optional strict fail-fast mode; when enabled, missing provider credentials make readiness `not_ready`)
- Existing Stripe vars remain required in production billing flows:
  - `STRIPE_SECRET_KEY`
  - `STRIPE_WEBHOOK_SECRET`

### “System ready” criteria for production

Before declaring production fully ready:

1. Stripe health checks pass (secret + webhook secret + configured plan price IDs).
2. Email provider is configured with valid sender/from address and test-send succeeds.
3. At least one Slack integration is configured and test-send succeeds.
4. Startup validation reports no production errors.

## Always-on monitoring worker

Run migrations and start the monitoring worker in a separate process:

```bash
python services/api/scripts/migrate.py
python -m services.api.app.run_monitoring_worker --interval-seconds 15
```

Optional env vars:
- `MONITORING_WORKER_NAME` (default: `monitoring-worker`)
- `MONITORING_ALERT_DEDUPE_WINDOW_SECONDS` (default: `900`)
- `THREAT_ENGINE_URL` and `THREAT_ENGINE_TIMEOUT_SECONDS`

In production deploy three processes: web app, API, and monitoring worker (`python -m services.api.app.run_monitoring_worker`).

## Production hardening updates (March 31, 2026)

### Deterministic-by-default test split
- Default suite: `pytest -q` (no `EVM_RPC_URL` required).
- Opt-in live monitoring verification:
  - `MONITORING_INGESTION_MODE=live EVM_RPC_URL=https://... pytest -q services/api/tests/test_monitoring_config_modes.py`

### MFA end-user flows
Frontend now supports:
- MFA challenge completion during sign-in.
- MFA enrollment in **Settings → Security**.
- Copyable `otpauth://` URI + secret.
- Recovery codes displayed once at enrollment confirmation.
- MFA disable with current TOTP code.

### Production safety defaults
- `/health/readiness` now reports production readiness with blocking errors.
- Production now treats these as blocking misconfiguration:
  - `EMAIL_PROVIDER=console`
  - missing `REDIS_URL` (shared auth throttling required)

### One-command production validation
Run:

```bash
make validate-production
```

This executes deterministic auth/MFA, monitoring, Slack/webhook routing, export/operability, and readiness checks via backend tests plus frontend MFA source assertions.

### Ops hardening artifacts
- Incident + rotation runbook: `docs/OPERATIONS_RUNBOOK.md`
- Readiness endpoint: `GET /health/readiness`
- Monitoring worker health: `GET /ops/monitoring/health`

### Next.js / npm note
This repository validates Next.js package/lockfile/runtime alignment with `python scripts/check_frontend_runtime_alignment.py` and treats runtime drift as a launch blocker.

## Strict production-required API environment variables

When `APP_ENV=production` and `LIVE_MODE_ENABLED=true`, startup validation now requires:

- `DATABASE_URL`
- `AUTH_TOKEN_SECRET`
- `EMAIL_PROVIDER` (must not be `console`)
- `EMAIL_FROM`
- provider key(s): `EMAIL_RESEND_API_KEY` when `EMAIL_PROVIDER=resend`
- `REDIS_URL`
- billing credentials are optional by default (startup remains healthy for core product when `BILLING_PROVIDER=none` or billing credentials are missing).
- if strict billing is enabled (`STRICT_PRODUCTION_BILLING=true`), readiness becomes `not_ready` until provider credentials are present.

Readiness + diagnostics endpoints:

- `GET /health/readiness` → `healthy`, `degraded`, or `not_ready` + top-level `billing` diagnostics (`provider`, `status`, `available`, checks).
- `GET /health/diagnostics` → machine-readable per-check pass/fail output (secret-safe), including billing diagnostics.

## Launch validation commands

Canonical launch-gate commands:

```bash
make validate-production
make validate-staging
make validate-launch

make validate-paid-ga
```

These gates emit machine-readable JSON and an operator summary across six categories:

1. `local_repo_integrity`
2. `frontend_build_reproducibility`
3. `browser_e2e_runtime`
4. `api_runtime_readiness`
5. `live_provider_configuration`
6. `staging_evidence`

Use `make install-web-test-runtime` if Playwright package/browsers are missing.

## Billing verification notes

Deterministic billing tests now cover:

- checkout session creation contract
- webhook signature validation
- webhook replay/idempotency behavior
- subscription/customer reconciliation writes
- portal session failure when customer linkage is missing

Run with:

```bash
pytest -q services/api/tests/test_billing_runtime.py
```

For live webhook checks in staging, forward Stripe events to `/billing/webhooks/stripe` and confirm events show as processed without duplicate state updates.

## Live-provider smoke checks

Run:

```bash
python services/api/scripts/smoke_live_providers.py
```

The smoke report now classifies each provider check as `verified`, `configured_unverified`, `not_configured`, or `fail` for:

- Email provider and sender domain setup
- Billing provider coherence (Paddle/Stripe credentials + configuration)
- Redis production requirement enforcement
- Staging API `/health/readiness` response
- EVM RPC reachability + `eth_chainId` probe when `EVM_RPC_URL` is set

## E2E browser test path

Use `make install-web-test-runtime` to install deterministic browser runtime prerequisites.

Then run:

```bash
python scripts/run_web_local_smoke.py
python scripts/staging/run_evidence_flow.py
```

For no-billing pilot validation without staging credentials, `validate-no-billing-launch` records staging evidence as skipped-missing-configuration instead of failing the full gate. To produce full staging evidence artifacts, provide the `STAGING_*` variables and rerun `npm run evidence:staging`.

For a single reproducible operator run (including `npm ci`, `npm run build:web`, and `make validate-no-billing-launch`), run:

```bash
npm run proof:no-billing-launch
```

Expected artifact outputs:
- `artifacts/launch-proof/<timestamp>/summary.md`
- `artifacts/launch-proof/<timestamp>/summary.json`
- command logs like `01_npm_ci.log`, `02_build_web.log`, and `03_validate_no_billing_launch.log`
- optional staging evidence output at `artifacts/launch-proof/<timestamp>/staging-evidence/` when `STAGING_*` credentials are provided

Troubleshooting notes:
- If Playwright Chromium download is blocked on your runner, no-billing pilot mode keeps validation explicit by recording browser-runtime checks as `SKIP` (not fake `PASS`), while still failing real blockers.
- If `STAGING_*` vars are missing, staging evidence is intentionally recorded as skipped configuration. Provide env vars and rerun for full staging artifacts.

Validation explicitly separates dependency-missing, browser-binaries-missing, and true E2E execution failures. The local smoke wrapper writes startup logs to `artifacts/web-local-smoke/next-web.log` for deterministic CI debugging.


## Minimal launch runbook (Vercel + Railway)

### Vercel (apps/web)
- Set project root to `apps/web`.
- Required envs: `NEXT_PUBLIC_LIVE_MODE_ENABLED`, and one of `API_URL` or `NEXT_PUBLIC_API_URL` (prefer `API_URL`).
- Deploy and verify build output before running staging evidence.

### Railway (services/api)
- Required baseline envs: `APP_ENV=production`, `LIVE_MODE_ENABLED=true`, `DATABASE_URL`, `AUTH_TOKEN_SECRET`, `REDIS_URL`.
- Email launch path: `EMAIL_PROVIDER=resend`, `EMAIL_FROM`, `EMAIL_RESEND_API_KEY`.
- Billing launch path: `BILLING_PROVIDER=paddle|stripe` + matching webhook/API secrets.

### Billing webhooks
- Paddle: point to `POST /billing/webhooks/paddle`.
- Stripe: point to `POST /billing/webhooks/stripe`.
- Re-run `python services/api/scripts/smoke_live_providers.py` after webhook secret changes.

### MFA status note
- Backend MFA is implemented and validated.
- Frontend MFA flows exist for sign-in challenge and settings enrollment; enterprise auth controls (for example SSO/SCIM) remain future work.

See `docs/LAUNCH_VALIDATION_CHECKLIST.md` for pilot vs broad-sale vs enterprise gating criteria.

## Demo mode vs Live mode

- **Demo mode (`MONITORING_INGESTION_MODE=demo`)** uses deterministic sample scenarios for product walkthroughs only.
- **Live mode (`MONITORING_INGESTION_MODE=live`)** requires `EVM_RPC_URL` and only processes real chain events.
- **Hybrid mode (`MONITORING_INGESTION_MODE=hybrid`, recommended)** prefers websocket ingestion when `EVM_WS_URL` is present, with polling/RPC backfill continuity and explicit degraded status reporting.
- **Degraded mode (`DEGRADED`)** is an operational state exposed by `/ops/monitoring/health` and `/ops/monitoring/runtime-status` whenever live/hybrid runtime checks fail (provider unreachable, live monitoring disabled, stale checkpoints, or source degradation).
- In authenticated live workspaces, wallet/contract monitoring does **not** silently inject demo payloads if live ingestion is unavailable.
- Live/hybrid monitoring never fabricates synthetic-success analysis when providers fail; failures are persisted as degraded/failed evidence records.
- Missing provider data is persisted as explicit `no_evidence`/`degraded`/`failed` truth states and never mapped to safe/normal defaults.
- No recent alert is not proof of safety; it can mean no evidence, degraded monitoring, or unknown risk.
- “No confirmed anomaly detected in observed evidence” is an observation label, not a global safety claim.
- LIVE/HYBRID reassurance requires real observed evidence (`recent_evidence_state=real`, `recent_real_event_count>0`, and non-`unknown_risk` truthfulness).
- `/ops/production-claim-validator` now fails if synthetic/demo evidence leaks into the recent evidence window.
- See `docs/DEMO_ISOLATION_AUDIT.md` for the file-by-file synthetic fallback removals and isolation boundaries.

## How to prove live on-chain monitoring

1. Configure `LIVE_MONITORING_ENABLED=true`, `MONITORING_INGESTION_MODE=hybrid`, and a reachable `EVM_RPC_URL`.
2. Add a workspace target with wallet/contract address and chain mapping (`chain_network`, `chain_id`).
3. Run monitoring and verify checkpoint advancement (`monitoring_checkpoint_at/cursor`, `watcher_last_observed_block`, lag, source status).
4. Confirm persisted evidence across tables/feeds:
   - `monitoring_event_receipts`
   - `analysis_runs`
   - `alerts` and `incidents`
   - `audit_logs`
5. Run `python services/api/scripts/run_live_claim_check.py` and ensure `/ops/production-claim-validator` returns `PASS`.
6. Run `python services/api/scripts/run_live_evidence_flow.py` to generate an operator-facing end-to-end evidence bundle.

## What claims are valid to make publicly

You may publicly claim **Strategic Infrastructure Guard live protection** only when:

- monitoring mode is `live` or `hybrid`;
- live monitoring is enabled and `EVM_RPC_URL` is reachable;
- watcher source is active (`websocket`, `polling`, or `rpc_backfill`);
- checkpoints are advancing with recent events;
- degraded conditions are surfaced and auditable (no silent demo substitution).

If any check fails, public claims must remain limited to pilot/demo capabilities.
