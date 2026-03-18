# Phase 1 Tokenized Treasury Risk-Control Monorepo

This repo now supports a reproducible local Phase 1 workflow without Docker as the primary path. The stable Phase 1 risk-engine remains intact, and Feature 2 adds a new `threat-engine` service for explainable zero-day exploit mitigation and treasury-token market anomaly detection.

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
python scripts/run_service.py risk-engine --reload
python scripts/run_service.py threat-engine --reload
python scripts/run_service.py api --reload
npm run dev --workspace apps/web
```

Open:

- API docs: `http://localhost:8000/docs`
- Risk-engine docs: `http://localhost:8001/docs`
- Threat-engine docs: `http://localhost:8002/docs`
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

### 7) Run the API gateway in a third terminal

```cmd
.venv\Scripts\activate
python scripts\run_service.py api --reload
```

### 8) Run the frontend in a fourth terminal

```cmd
.venv\Scripts\activate
npm run dev --workspace apps/web
```

### 9) Open the local apps

- API docs: `http://localhost:8000/docs`
- Risk-engine docs: `http://localhost:8001/docs`
- Threat-engine docs: `http://localhost:8002/docs`
- Dashboard: `http://localhost:3000`

## Local backend behavior

- `services/api` defaults to `RISK_ENGINE_URL=http://localhost:8001` and `THREAT_ENGINE_URL=http://localhost:8002`.
- `services/api /risk/dashboard` prefers live risk-engine evaluations.
- `services/api /threat/dashboard` prefers live threat-engine data for Feature 2 cards, alerts, and detections.
- If either backend is unavailable or times out, the API returns explicit fallback-safe dashboard data instead of failing the UI.
- The dashboard renders both live and degraded states without blanking the page.
- Browser demo interactions call the API gateway, not the backend workers directly, so the Windows CMD workflow remains repo-root friendly.

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
