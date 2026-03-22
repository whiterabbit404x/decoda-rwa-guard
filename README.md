# Phase 1 Tokenized Treasury Risk-Control Monorepo

This repo now supports a reproducible local Phase 1 workflow without Docker as the primary path. The stable Phase 1 risk-engine remains intact, Feature 2 adds the `threat-engine` service for explainable zero-day exploit mitigation and treasury-token market anomaly detection, Feature 3 adds the `compliance-service` for sovereign-grade compliance wrappers, geopatriation controls, and governance actions, and Feature 4 expands the existing `reconciliation-service` into an interoperability and systemic resilience slice for deterministic cross-chain reconciliation, backstop controls, and local incident logging.

## Pilot SaaS mode (Railway + Vercel + Neon)

The repo now supports a minimal **real pilot SaaS mode** alongside the existing local/demo mode. Demo endpoints and UI still work, but authenticated live-mode actions persist workspace-scoped data through `services/api` into Neon Postgres. The backend deployment model remains the same: Railway still runs the existing API Dockerfile, and Vercel still hosts the Next.js frontend.

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
| Production | `NEXT_PUBLIC_LIVE_MODE_ENABLED=true`, `API_URL=https://<your-railway-api>.up.railway.app` (preferred) or `NEXT_PUBLIC_API_URL=https://<your-railway-api>.up.railway.app`, `NEXT_PUBLIC_API_TIMEOUT_MS=5000` | `next build` now fails clearly if `NEXT_PUBLIC_LIVE_MODE_ENABLED` or both API URL vars are missing. |
| Preview | `NEXT_PUBLIC_LIVE_MODE_ENABLED=true`, `API_URL=https://<preview-or-shared-railway-api>.up.railway.app` (preferred) or `NEXT_PUBLIC_API_URL=https://<preview-or-shared-railway-api>.up.railway.app`, `NEXT_PUBLIC_API_TIMEOUT_MS=5000` | Preview auth pages surface a warning banner and `/api/build-info` reports the branch, commit SHA, Vercel environment, and runtime-config summary for that deployment. |
| Development | `NEXT_PUBLIC_LIVE_MODE_ENABLED=false` (or `true` if you are exercising pilot mode locally), `NEXT_PUBLIC_API_URL=http://127.0.0.1:8000`, optional `NEXT_PUBLIC_API_TIMEOUT_MS=5000` | Local development still falls back to localhost defaults, but explicitly setting the vars keeps local behavior aligned with Vercel. |

Recommended Vercel project settings:

- **Root Directory:** `apps/web`
- **Framework Preset:** Next.js
- **Install Command:** repo-root install is fine, but the web project itself must still build from `apps/web` inside the monorepo.

### Migrations and seed commands

Run these from the repo root after setting `DATABASE_URL` and `AUTH_TOKEN_SECRET` for the API service:

```bash
python services/api/scripts/migrate.py
python services/api/scripts/seed.py --pilot-demo
```

The pilot demo seed creates a workspace-scoped demo account in Postgres while preserving the original local SQLite registry seed for demo mode. You can override the seeded credentials with `--demo-email`, `--demo-password`, `--demo-workspace`, and `--demo-full-name`.

### Railway deploy / update flow

1. Keep deploying `services/api/Dockerfile` on Railway.
2. Keep the Docker build context at the repo root so `services/api/Dockerfile` can `COPY` sibling fixture folders such as `services/risk-engine/data` and `services/reconciliation-service/data`.
3. After each deploy, verify the running container via `GET /health/details` or the startup log line that now prints the runtime version marker plus fixture-path existence checks.
4. Set Railway env vars from `services/api/.env.example`.
5. Run migrations against Neon with `python services/api/scripts/migrate.py` in Railway's shell or a one-off command using the same image/env.
6. Optionally run `python services/api/scripts/seed.py --pilot-demo` once for a demo tenant.
7. Keep the Railway start command binding to `0.0.0.0:$PORT` (already handled by the Dockerfile).

### Vercel setup flow

1. Keep deploying `apps/web` on Vercel.
2. Set Vercel env vars from `apps/web/.env.example`.
3. Redeploy after updating `NEXT_PUBLIC_API_URL` or `NEXT_PUBLIC_LIVE_MODE_ENABLED`.
4. Use `/sign-up`, `/sign-in`, and `/workspaces` for live pilot onboarding while `/` continues to expose the existing dashboard.

### Why preview can fail while production works

Preview deploys are more likely to fail than production in this monorepo because Vercel treats **environment scopes**, **root-directory settings**, and **branch-specific config** separately:

- Production can keep working while Preview is broken if Preview is missing `NEXT_PUBLIC_LIVE_MODE_ENABLED`, `API_URL`, or `NEXT_PUBLIC_API_URL`.
- Production can keep working while Preview is broken if the Vercel project root points somewhere other than `apps/web`, because the monorepo build will resolve a different working directory during `next build`.
- Preview can fail at build time even before auth traffic reaches Railway, while production appears healthy, because the web app now validates required Vercel config during `next build`.
- Preview can also reach runtime with the wrong backend target if branch-specific env vars drift. In that case, `/api/build-info` tells operators whether the issue is the build, the preview environment, or the resolved runtime config.

Fast operator checks:

1. Confirm the Vercel project **Root Directory** is `apps/web`.
2. Confirm the Preview environment in Vercel has `NEXT_PUBLIC_LIVE_MODE_ENABLED` and one of `API_URL` / `NEXT_PUBLIC_API_URL`.
3. Open `/api/build-info` on the preview deployment and verify `vercelEnv`, `branch`, `commitSha`, and the runtime config summary.
4. If `/api/build-info` is healthy but auth still fails, the next place to check is the backend API / Railway deployment rather than the same-origin auth proxy.

### What remains for full production later

- Email verification, password reset, MFA, and server-side session invalidation.
- Stronger distributed rate limiting / shared cache instead of in-memory auth throttling.
- Richer RBAC enforcement across every workflow action.
- Background jobs, webhooks, and more granular per-record dashboards instead of summary persistence only.
- Managed observability, secret rotation, and tenant billing / provisioning workflows.

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
2. In **Feature 3 demo interactions**, leave **Compliant transfer approved** selected and click **Run transfer screening**.
3. Switch to **Blocked by sanctions** or **Review for incomplete KYC** and run the transfer screening again to compare deterministic wrapper reasons.
4. In the residency section, run **Denied restricted region** to verify the geopatriation controls and routing recommendation.
5. In the governance section, submit **Freeze wallet**, **Pause asset**, or **Allowlist wallet**.
6. After the page refreshes, verify the new governance record appears under **Latest governance actions** and that **Asset transfer status** / policy state cards reflect the updated state.

### Feature 4 demo walkthrough — reconciliation, backstops, and incidents

1. Scroll to **Feature 4 · Interoperability & Systemic Resilience**.
2. In **Feature 4 demo interactions**, run **Critical divergence** to compare expected supply vs. observed multi-ledger supply.
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
- `/dashboard`, `/threat`, `/compliance`, `/resilience`, `/history`, and `/settings` are the authenticated product routes.
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
  - **Preview:** `NEXT_PUBLIC_LIVE_MODE_ENABLED=true`, `API_URL=https://<preview-or-shared-railway-api>.up.railway.app` (preferred) or `NEXT_PUBLIC_API_URL=https://<preview-or-shared-railway-api>.up.railway.app`, and `NEXT_PUBLIC_API_TIMEOUT_MS=5000`.
  - **Development:** `NEXT_PUBLIC_LIVE_MODE_ENABLED=false` (or `true` when testing pilot mode locally), `NEXT_PUBLIC_API_URL=http://127.0.0.1:8000`, and optional `NEXT_PUBLIC_API_TIMEOUT_MS=5000`.
- Preview and production builds now fail clearly when `NEXT_PUBLIC_LIVE_MODE_ENABLED` is missing or when both API URL variables are absent.
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
- [ ] Vercel Production and Preview both set `NEXT_PUBLIC_LIVE_MODE_ENABLED` and at least one of `API_URL` / `NEXT_PUBLIC_API_URL`, plus `NEXT_PUBLIC_API_TIMEOUT_MS`.
- [ ] Vercel Root Directory is `apps/web`.
- [ ] `/api/build-info` reports the expected `vercelEnv`, branch, commit SHA, and runtime config summary on the deployed site.
- [ ] `python services/api/scripts/migrate.py` has been run against Neon.
- [ ] Optional pilot seed completed with `python services/api/scripts/seed.py --pilot-demo`.
- [ ] `/health/details` confirms expected dependency and runtime mode.
- [ ] `/` shows the marketing homepage and `/dashboard` opens the authenticated product experience.
