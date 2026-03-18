# Phase 1 Tokenized Treasury Risk-Control Monorepo

This repo now supports a reproducible local Phase 1 workflow without Docker as the primary path. The backend uses a shared SQLite file, the API gateway pulls live risk data from the risk-engine when it is healthy, and the web dashboard falls back cleanly when backend services are unavailable.

## Repository Layout

- `apps/web` — Next.js dashboard UI.
- `services/api` — FastAPI gateway for dashboard and risk feed aggregation.
- `services/risk-engine` — FastAPI risk scoring service with Phase 1 heuristics.
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
make init-local
make run-risk
make run-api
make run-web
```

Open:

- API docs: `http://localhost:8001/docs`
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
```

### 5) Run the risk-engine

```cmd
python scripts\run_service.py risk-engine --reload
```

### 6) Run the API gateway in a second terminal

```cmd
.venv\Scripts\activate
python scripts\run_service.py api --reload
```

### 7) Run the frontend in a third terminal

```cmd
.venv\Scripts\activate
npm run dev --workspace apps/web
```

### 8) Open the local apps

- Risk-engine docs: `http://localhost:8001/docs`
- Dashboard: `http://localhost:3000`

## Local backend behavior

- `services/api` defaults to `RISK_ENGINE_URL=http://localhost:8001`.
- `services/api /risk/dashboard` prefers live risk-engine evaluations.
- If the risk-engine is unavailable or times out, the API returns explicit fallback-safe dashboard data instead of failing the UI.
- The dashboard renders both live and degraded states without blanking the page.

## Risk endpoints and sample request bodies

### 1) Health / status endpoint

- `GET http://localhost:8001/health`
- `GET http://localhost:8001/state`

Example:

```cmd
curl http://localhost:8001/health
curl http://localhost:8001/state
```

### 2) List bundled scenarios

- `GET http://localhost:8001/v1/risk/scenarios`

```cmd
curl http://localhost:8001/v1/risk/scenarios
```

### 3) Fetch the bundled sample request body

- `GET http://localhost:8001/v1/risk/scenarios/sample-request`

```cmd
curl http://localhost:8001/v1/risk/scenarios/sample-request
```

### 4) Risk score endpoint

- `POST http://localhost:8001/v1/risk/evaluate`

Sample request body:

```json
{
  "transaction_payload": {
    "tx_hash": "0xphase1sample",
    "from_address": "0x1111111111111111111111111111111111111111",
    "to_address": "0x2222222222222222222222222222222222222222",
    "value": 1850000.0,
    "gas_price": 57.0,
    "gas_limit": 900000,
    "chain_id": 1,
    "calldata_size": 644,
    "token_transfers": [
      {"token": "USTB", "amount": 550000},
      {"token": "WETH", "amount": 1200},
      {"token": "WBTC", "amount": 40},
      {"token": "USDC", "amount": 300000}
    ],
    "metadata": {
      "contains_flash_loan_hop": true,
      "entrypoint": "aggregator-router"
    }
  },
  "decoded_function_call": {
    "function_name": "flashLoan",
    "contract_name": "LiquidityRouter",
    "arguments": {
      "receiver": "0x3333333333333333333333333333333333333333",
      "owner": "0x4444444444444444444444444444444444444444",
      "assets": ["USTB", "WETH"]
    },
    "selectors": ["0xabcd1234"]
  },
  "wallet_reputation": {
    "address": "0x1111111111111111111111111111111111111111",
    "score": 22,
    "prior_flags": 3,
    "account_age_days": 5,
    "kyc_verified": false,
    "sanctions_hits": 0,
    "known_safe": false,
    "recent_counterparties": 27,
    "metadata": {"watchlist": "elevated"}
  },
  "contract_metadata": {
    "address": "0x2222222222222222222222222222222222222222",
    "contract_name": "LiquidityRouter",
    "verified_source": false,
    "proxy": true,
    "created_days_ago": 3,
    "tvl": 9000000.0,
    "audit_count": 0,
    "categories": ["dex", "router"],
    "static_flags": {
      "uses_delegatecall": true,
      "external_call_in_loop": true,
      "obfuscated_storage": true
    },
    "metadata": {"deployer_reputation": "unknown"}
  },
  "recent_market_events": []
}
```

Example call:

```cmd
curl -X POST http://localhost:8001/v1/risk/evaluate ^
  -H "Content-Type: application/json" ^
  --data @services\risk-engine\data\sample_risk_request.json
```

### 5) Scenario / simulation endpoint

The scenario replay flow keeps endpoint naming unchanged by using the bundled scenario loader plus the evaluate endpoint:

```cmd
curl http://localhost:8001/v1/risk/scenarios/suspicious
curl -X POST http://localhost:8001/v1/risk/evaluate ^
  -H "Content-Type: application/json" ^
  --data @services\risk-engine\data\sample_risk_request.json
```

### 6) Dashboard-related endpoint

- `GET http://localhost:8000/risk/dashboard`

This endpoint returns:

- `source: "live"` when all queue items are evaluated by the live risk-engine.
- `source: "fallback"` with `degraded: true` and a UI-safe `message` when the risk-engine is unavailable.

```cmd
curl http://localhost:8000/risk/dashboard
```

## OpenAPI docs

When the risk-engine is running locally, the interactive docs are available at:

- `http://localhost:8001/docs`

The docs now include endpoint summaries/descriptions plus request-body examples for the main evaluation endpoints.

## Validation / smoke check

Run the lightweight smoke check from the repo root:

```cmd
python scripts\smoke_phase1.py
```

The smoke check proves that:

- the risk-engine starts,
- the API starts,
- the API fetches live risk-engine data, and
- the API returns a frontend-safe fallback shape when the risk-engine becomes unavailable.

## Optional Docker support

Docker remains available as an optional workflow through `docker-compose.yml`, but it is no longer the primary local path.

```bash
make up
make logs
make down
```
