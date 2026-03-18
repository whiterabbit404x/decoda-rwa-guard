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

### 5. Run the frontend locally

In a second terminal:

```bash
make run-web
```

Then open `http://localhost:3000`.

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
