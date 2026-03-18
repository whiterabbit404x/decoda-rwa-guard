# Phase 1 Tokenized Treasury Risk-Control Monorepo

This monorepo bootstraps a Phase 1 risk-control platform for tokenized treasury operations.

## Repository Layout

- `apps/web` — Next.js + TypeScript frontend with placeholder dashboards.
- `services/api` — FastAPI entrypoint for platform APIs.
- `services/risk-engine` — Worker service for risk scoring and controls.
- `services/oracle-service` — Worker service for ingesting oracle market data.
- `services/compliance-service` — Worker service for policy/compliance checks.
- `services/reconciliation-service` — Worker service for ledger reconciliation.
- `services/event-watcher` — Worker service for on-chain/off-chain event ingestion.
- `packages/shared-types` — Shared TypeScript models consumed by frontend/services.
- `contracts/core` — Solidity contracts and Foundry config.

## Quickstart

1. Copy env files:
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
2. Start infrastructure:
   ```bash
   make up
   ```
3. Run services locally (in separate shells):
   ```bash
   make run-api
   make run-risk
   make run-oracle
   make run-compliance
   make run-reconciliation
   make run-event-watcher
   make run-web
   ```

## Seed Scripts

Each Python service includes a seed script under `scripts/seed.py` to initialize sample state.

```bash
make seed-all
```

## Health Endpoints

- API: `GET /health`
- Risk Engine: `GET /health`
- Oracle Service: `GET /health`
- Compliance Service: `GET /health`
- Reconciliation Service: `GET /health`
- Event Watcher: `GET /health`

## Contracts

Foundry project is in `contracts/core`.

```bash
cd contracts/core
forge build
forge test
```
