# Phase 1 Tokenized Treasury Risk-Control Monorepo

This monorepo now supports a lightweight local development flow that does **not** require Docker. The default local mode uses a shared SQLite database file and runs every backend service directly with FastAPI/Uvicorn while keeping the existing monorepo structure intact.

## Repository Layout

- `apps/web` — Next.js frontend for the local dashboard.
- `services/api` — FastAPI gateway that exposes the dashboard and service registry APIs.
- `services/risk-engine` — Risk scoring worker.
- `services/oracle-service` — Oracle data worker.
- `services/compliance-service` — Compliance worker.
- `services/reconciliation-service` — Reconciliation worker.
- `services/event-watcher` — Event ingestion worker.
- `packages/shared-types` — Shared TypeScript models consumed by frontend/services.
- `contracts/core` — Solidity contracts and Foundry config.
- `phase1_local` — Shared Python helpers for local SQLite-backed development.

## Local Development Quickstart (No Docker Required)

### 1. Copy the example environment files

```bash
cp .env.example .env
cp apps/web/.env.example apps/web/.env.local
cp services/api/.env.example services/api/.env
cp services/risk-engine/.env.example services/risk-engine/.env
cp services/oracle-service/.env.example services/oracle-service/.env
cp services/compliance-service/.env.example services/compliance-service/.env
cp services/reconciliation-service/.env.example services/reconciliation-service/.env
cp services/event-watcher/.env.example services/event-watcher/.env
```

### 2. Install dependencies

Python dependencies for all backend services:

```bash
python -m venv .venv
source .venv/bin/activate
make install-python
```

Frontend dependencies:

```bash
make install-web
```

### 3. Initialize the local SQLite dataset

```bash
make init-local
```

This creates `.data/phase1.db` and seeds sample service state for the dashboard. Redis is disabled in local mode and is not required.

### 4. Run the backend locally

Start the entire backend stack in one terminal:

```bash
make run-backend
```

This starts:

- API on `http://localhost:8000`
- Risk Engine on `http://localhost:8001`
- Oracle Service on `http://localhost:8002`
- Compliance Service on `http://localhost:8003`
- Reconciliation Service on `http://localhost:8004`
- Event Watcher on `http://localhost:8005`

You can also run a single service with the existing `make run-api`, `make run-risk`, `make run-oracle`, `make run-compliance`, `make run-reconciliation`, and `make run-event-watcher` targets.

### Windows CMD: run from the repo root

These commands avoid `PYTHONPATH` shell tricks and run cleanly from the repository root after `git clone` / `git pull`.

```cmd
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements-local.txt
python services\api\scripts\seed.py
python services\risk-engine\scripts\seed.py
python scripts\run_service.py api --reload
```

In a second terminal:

```cmd
.venv\Scripts\activate
python scripts\run_service.py risk-engine --reload
```

If you want the full backend stack instead, use:

```cmd
.venv\Scripts\activate
python scripts\run_local_backend.py
```

### 5. Run the frontend locally

In a second terminal:

```bash
make run-web
```

Then open `http://localhost:3000`.

## Risk Engine Local API Examples

The risk engine serves OpenAPI docs at `http://localhost:8001/docs` and supports both reusable scenario payloads and direct evaluations.

### List bundled scenarios

```bash
curl http://localhost:8001/v1/risk/scenarios
```

### Fetch the sample request body

```bash
curl http://localhost:8001/v1/risk/scenarios/sample-request
```

### Sample `POST /v1/risk/evaluate` request body

```json
{
  "transaction_payload": {
    "tx_hash": "0xphase1sample",
    "from_address": "0x1111111111111111111111111111111111111111",
    "to_address": "0x2222222222222222222222222222222222222222",
    "value": 2500000.0,
    "gas_price": 95.0,
    "token_transfers": [
      {
        "token": "USTB",
        "amount": 2500000
      }
    ],
    "metadata": {
      "contains_flash_loan_hop": true,
      "entrypoint": "router"
    }
  },
  "decoded_function_call": {
    "function_name": "flashLoan",
    "contract_name": "LiquidityRouter",
    "arguments": {
      "assets": [
        "USTB"
      ],
      "amounts": [
        2500000
      ],
      "receiver": "0x3333333333333333333333333333333333333333"
    },
    "selectors": [
      "0xa9059cbb"
    ]
  },
  "wallet_reputation": {
    "address": "0x1111111111111111111111111111111111111111",
    "score": 28,
    "prior_flags": 3,
    "account_age_days": 21,
    "kyc_verified": false,
    "known_safe": false,
    "recent_counterparties": 37,
    "metadata": {
      "analyst_tag": "watchlist"
    }
  },
  "contract_metadata": {
    "address": "0x2222222222222222222222222222222222222222",
    "category": "dex-router",
    "is_proxy": true,
    "is_verified": false,
    "audit_count": 0,
    "upgradeability": "mutable"
  },
  "recent_market_events": [
    {
      "event_type": "liquidity_drop",
      "severity": "critical",
      "value": 0.71,
      "metadata": {
        "pool": "USTB/USDC"
      }
    },
    {
      "event_type": "cancel_burst",
      "severity": "high",
      "value": 184.0,
      "metadata": {
        "venue": "synthetic-exchange"
      }
    }
  ]
}
```

### Evaluate the sample payload

```bash
curl -X POST http://localhost:8001/v1/risk/evaluate \
  -H "Content-Type: application/json" \
  --data @services/risk-engine/data/sample_risk_request.json
```

### Dashboard risk feed

The frontend reads the API gateway, and the API gateway calls the risk engine for live queue evaluations:

```bash
curl http://localhost:8000/risk/dashboard
```

## Local API Endpoints

- API health: `GET /health`
- API dashboard payload: `GET /dashboard`
- API service registry: `GET /services`
- Every worker service: `GET /health`
- Every worker service: `GET /state`

## Optional Docker Support

Docker remains available as an **optional** workflow through `docker-compose.yml`, but it is no longer required for everyday local development. The primary local path is the SQLite-backed setup above.

```bash
make up
make logs
make down
```

## Seed Scripts

Each Python service includes a seed script under `scripts/seed.py` to initialize or refresh local sample state.

```bash
make seed-all
```

## Contracts

Foundry project is in `contracts/core`.

```bash
cd contracts/core
forge build
forge test
```
