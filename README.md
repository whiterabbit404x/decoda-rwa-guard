# Phase 1 Tokenized Treasury Risk-Control Monorepo

This repo now supports a reproducible local Phase 1 workflow without Docker as the primary path. The stable Phase 1 risk-engine remains intact, Feature 2 adds the `threat-engine` service for explainable zero-day exploit mitigation and treasury-token market anomaly detection, Feature 3 adds the `compliance-service` for sovereign-grade compliance wrappers, geopatriation controls, and governance actions, and Feature 4 expands the existing `reconciliation-service` into an interoperability and systemic resilience slice for deterministic cross-chain reconciliation, backstop controls, and local incident logging.

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
python services\compliance-service\scripts\seed.py
python services\reconciliation-service\scripts\seed.py
```

### 5) Run the risk-engine

```cmd
python scripts\run_service.py risk-engine --reload
```

### 6) Run the threat-engine in a second terminal

```cmd
.venv\Scripts\activate
python scripts\run_service.py threat-engine --reload
```

### 7) Run the compliance-service in a third terminal

```cmd
.venv\Scripts\activate
python scripts\run_service.py compliance-service --reload
```

### 8) Run the reconciliation-service in a fourth terminal

```cmd
.venv\Scripts\activate
python scripts\run_service.py reconciliation-service --reload
```

### 9) Run the API gateway in a fifth terminal

```cmd
.venv\Scripts\activate
python scripts\run_service.py api --reload
```

### 10) Run the frontend in a sixth terminal

```cmd
.venv\Scripts\activate
npm run dev --workspace apps/web
```

### 11) Open the local apps

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
