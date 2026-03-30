# Phase 1 Platform Architecture

## Goals

Phase 1 provides a modular control plane for tokenized treasury operations. The platform is optimized for:

- deterministic local development with SQLite and FastAPI services,
- service isolation by domain responsibility,
- shared contracts for web, API, and worker services,
- event-driven orchestration with auditable decisions, and
- safety controls through compliance, risk, oracle consensus, and circuit breakers.

## Service Boundaries

### 1. API Gateway (`services/api`)

**Responsibilities**
- external entry point for dashboard and operator traffic,
- request validation and authentication integration point,
- orchestration of transfer requests,
- read aggregation for investigation, alert, and operational views,
- synchronous status reads from downstream services.

**Owns**
- public REST surface,
- request/response normalization,
- correlation IDs and idempotency keys.

**Does not own**
- risk scoring logic,
- compliance policy evaluation,
- oracle price production,
- ledger reconciliation,
- chain event ingestion.

### 2. Event Watcher (`services/event-watcher`)

**Responsibilities**
- ingest treasury controller and token contract events,
- normalize on-chain events into internal event envelopes,
- publish transfer lifecycle triggers,
- persist provenance records for raw blockchain observations.

**Owns**
- block/transaction/log cursors,
- event normalization,
- chain-originated provenance.

### 3. Risk Engine (`services/risk-engine`)

**Responsibilities**
- evaluate transfer and wallet risk,
- aggregate risk signals,
- produce risk decisions and recommended actions,
- emit alerts for elevated or critical risk.

**Owns**
- risk rule configuration,
- risk signal catalog,
- risk score computation,
- risk decision history.

### 4. Oracle Service (`services/oracle-service`)

**Responsibilities**
- collect market and reference data readings,
- validate freshness and source diversity,
- compute oracle consensus decisions,
- flag stale or divergent sources.

**Owns**
- oracle readings,
- consensus snapshots,
- source health and quorum policy.

### 5. Compliance Service (`services/compliance-service`)

**Responsibilities**
- perform sanctions and jurisdiction screening,
- evaluate wallet and transfer policy controls,
- create compliance decisions and case escalations,
- record hits and evidence references.

**Owns**
- sanctions hit artifacts,
- compliance policy versions,
- compliance decision history,
- investigation case creation for escalations.

### 6. Reconciliation Service (`services/reconciliation-service`)

**Responsibilities**
- compare on-chain transfers with internal ledger records,
- produce reconciliation events,
- detect breaks, missing postings, and settlement mismatches,
- close the audit loop after execution.

**Owns**
- reconciliation status,
- ledger parity findings,
- operational exception lifecycle.

### 7. Shared Types Packages (`packages/shared-types`, `phase1_local`)

**Responsibilities**
- define shared cross-service models,
- prevent schema drift between frontend and backend,
- provide importable TypeScript and Python equivalents.

**Owns**
- canonical field names,
- enums/status unions,
- model composition patterns used by every service.

## High-Level Runtime View

```text
Client / Dashboard
        |
        v
   API Gateway
        |
        +---------------------> Risk Engine
        |
        +---------------------> Compliance Service
        |
        +---------------------> Oracle Service
        |
        +---------------------> Reconciliation Service
        |
        +---------------------> Event Watcher
        |
        v
 Shared SQLite / future operational stores
        |
        v
 Audit, alerts, cases, decisions, provenance
```

## Request Flows

### A. Token Transfer Request Flow

1. Client submits `TokenTransferRequest` to API Gateway.
2. API validates schema, assigns `requestId`, `correlationId`, and idempotency key.
3. API loads source `Wallet` and destination `Wallet` context.
4. API requests latest `OracleConsensusDecision` to verify price/fx freshness.
5. API calls Risk Engine with transfer context and current wallet posture.
6. Risk Engine returns `RiskSignal[]` and a risk decision payload.
7. API calls Compliance Service with transfer, wallets, and provenance metadata.
8. Compliance Service returns `ComplianceDecision` and any `SanctionsHit[]`.
9. API evaluates `CircuitBreakerState` for the relevant asset, venue, or wallet segment.
10. If all controls pass, API marks request as `approved_for_execution` and emits an execution event.
11. If any control blocks, API marks request as `blocked` or `requires_review`, emits alerts, and optionally opens an investigation case.
12. Event Watcher later observes on-chain execution and hands off to Reconciliation Service.

### B. Investigation and Alert Flow

1. Risk, compliance, oracle, or reconciliation service emits an `Alert`.
2. API aggregates the alert feed for operators.
3. If severity is high or action is manual review, Compliance or Risk creates an `InvestigationCase`.
4. Operators record actions, attach evidence, and resolve or escalate the case.
5. All changes append to the audit log and provenance records.

### C. Circuit Breaker Activation Flow

1. Oracle Service detects stale data or unacceptable price divergence, or Risk Engine detects threshold breach.
2. Service emits a control event with proposed breaker scope.
3. API or control-plane logic updates `CircuitBreakerState`.
4. Subsequent transfer requests consult breaker state before approval.
5. Reconciliation and operator workflows must explicitly clear or expire the breaker.

## Event Flows

### Core Event Topics

- `transfer.requested`
- `risk.assessed`
- `oracle.reading.ingested`
- `oracle.consensus.computed`
- `compliance.screened`
- `transfer.approved`
- `transfer.blocked`
- `transfer.executed`
- `reconciliation.completed`
- `alert.created`
- `case.opened`
- `circuit_breaker.updated`
- `audit.recorded`

### Transfer Event Lifecycle

```text
transfer.requested
  -> oracle.consensus.computed
  -> risk.assessed
  -> compliance.screened
  -> transfer.approved | transfer.blocked | case.opened
  -> transfer.executed
  -> reconciliation.completed
  -> audit.recorded
```

### Event Envelope Requirements

Every event should include:

- `eventId`
- `eventType`
- `correlationId`
- `causationId`
- `entityType`
- `entityId`
- `occurredAt`
- `producer`
- `version`
- `payload`
- `provenance`

## Data Ownership and Persistence Model

## Live EVM Ingestion Modes and Production Claim Criteria

- `demo`: deterministic fixtures only (local demo/testing only).
- `live` + polling: continuously scans live `EVM_RPC_URL` blocks/logs with durable checkpoints.
- `live` + websocket: subscribes to `newHeads` + `logs` on `EVM_WS_URL`, reconnects with exponential backoff, and backfills gaps over RPC.
- `hybrid`: websocket-first with polling/RPC backfill fallback, while clearly marking degraded states.

### Required runtime env vars

- `EVM_RPC_URL`
- `EVM_WS_URL` (for websocket mode)
- `MONITORING_INGESTION_MODE` (`demo` | `live` | `hybrid`)
- `LIVE_MONITORING_ENABLED`
- `EVM_CONFIRMATIONS_REQUIRED`
- `EVM_BLOCK_LOOKBACK`
- `DATABASE_URL` (durable target loading + event receipts/checkpoints)

### When the production claim is valid

Claim “ingesting live on-chain activity and detecting real threats in production” only when:

1. live ingestion mode is enabled (`live` or `hybrid`) and `LIVE_MONITORING_ENABLED=true`;
2. event-watcher health is not degraded and source is active (`websocket` or `polling`);
3. recent live blocks are being processed and per-target checkpoints are advancing;
4. analysis failures are stored as failed/degraded (no silent fake-normal scoring in live mode);
5. degraded rate remains within the operator-accepted threshold.

SQLite is sufficient for Phase 1 local development, but the schemas below are designed so each table can later move into a service-owned operational database.

### Shared Operational Tables

#### `wallets`
- `wallet_id` TEXT PRIMARY KEY
- `address` TEXT UNIQUE NOT NULL
- `wallet_type` TEXT NOT NULL
- `owner_entity_id` TEXT NOT NULL
- `owner_entity_type` TEXT NOT NULL
- `custody_type` TEXT NOT NULL
- `jurisdiction` TEXT
- `risk_tier` TEXT NOT NULL
- `sanctions_screening_state` TEXT NOT NULL
- `enabled` BOOLEAN NOT NULL
- `metadata_json` TEXT NOT NULL
- `created_at` TEXT NOT NULL
- `updated_at` TEXT NOT NULL

#### `token_transfer_requests`
- `request_id` TEXT PRIMARY KEY
- `idempotency_key` TEXT UNIQUE NOT NULL
- `asset_symbol` TEXT NOT NULL
- `asset_address` TEXT
- `chain_id` INTEGER NOT NULL
- `amount` TEXT NOT NULL
- `currency` TEXT NOT NULL
- `source_wallet_id` TEXT NOT NULL
- `destination_wallet_id` TEXT NOT NULL
- `requested_by` TEXT NOT NULL
- `purpose` TEXT
- `client_reference` TEXT
- `status` TEXT NOT NULL
- `risk_decision_id` TEXT
- `compliance_decision_id` TEXT
- `oracle_consensus_id` TEXT
- `circuit_breaker_id` TEXT
- `provenance_record_id` TEXT
- `created_at` TEXT NOT NULL
- `updated_at` TEXT NOT NULL

#### `risk_signals`
- `signal_id` TEXT PRIMARY KEY
- `request_id` TEXT
- `wallet_id` TEXT
- `signal_type` TEXT NOT NULL
- `severity` TEXT NOT NULL
- `score` REAL NOT NULL
- `source` TEXT NOT NULL
- `summary` TEXT NOT NULL
- `evidence_json` TEXT NOT NULL
- `detected_at` TEXT NOT NULL
- `expires_at` TEXT

#### `risk_decisions`
- `risk_decision_id` TEXT PRIMARY KEY
- `request_id` TEXT NOT NULL
- `decision` TEXT NOT NULL
- `aggregated_score` REAL NOT NULL
- `max_signal_severity` TEXT NOT NULL
- `recommended_action` TEXT NOT NULL
- `policy_version` TEXT NOT NULL
- `reasons_json` TEXT NOT NULL
- `created_at` TEXT NOT NULL

#### `oracle_readings`
- `reading_id` TEXT PRIMARY KEY
- `asset_symbol` TEXT NOT NULL
- `source_name` TEXT NOT NULL
- `reading_type` TEXT NOT NULL
- `value` REAL NOT NULL
- `unit` TEXT NOT NULL
- `as_of` TEXT NOT NULL
- `confidence` REAL NOT NULL
- `deviation_bps` INTEGER
- `metadata_json` TEXT NOT NULL

#### `oracle_consensus_decisions`
- `consensus_id` TEXT PRIMARY KEY
- `asset_symbol` TEXT NOT NULL
- `decision` TEXT NOT NULL
- `consensus_value` REAL
- `allowed_deviation_bps` INTEGER NOT NULL
- `observed_spread_bps` INTEGER NOT NULL
- `minimum_sources` INTEGER NOT NULL
- `participating_sources_json` TEXT NOT NULL
- `rejected_sources_json` TEXT NOT NULL
- `stale` BOOLEAN NOT NULL
- `created_at` TEXT NOT NULL

#### `sanctions_hits`
- `hit_id` TEXT PRIMARY KEY
- `wallet_id` TEXT NOT NULL
- `provider` TEXT NOT NULL
- `match_score` REAL NOT NULL
- `matched_name` TEXT NOT NULL
- `list_name` TEXT NOT NULL
- `review_status` TEXT NOT NULL
- `evidence_json` TEXT NOT NULL
- `detected_at` TEXT NOT NULL

#### `compliance_decisions`
- `compliance_decision_id` TEXT PRIMARY KEY
- `request_id` TEXT NOT NULL
- `decision` TEXT NOT NULL
- `review_required` BOOLEAN NOT NULL
- `policy_version` TEXT NOT NULL
- `jurisdiction` TEXT
- `reasons_json` TEXT NOT NULL
- `sanctions_hit_ids_json` TEXT NOT NULL
- `created_at` TEXT NOT NULL

#### `provenance_records`
- `provenance_record_id` TEXT PRIMARY KEY
- `record_type` TEXT NOT NULL
- `source_system` TEXT NOT NULL
- `source_id` TEXT NOT NULL
- `hash` TEXT
- `uri` TEXT
- `captured_at` TEXT NOT NULL
- `observer` TEXT NOT NULL
- `metadata_json` TEXT NOT NULL

#### `reconciliation_events`
- `reconciliation_event_id` TEXT PRIMARY KEY
- `request_id` TEXT
- `ledger_reference` TEXT NOT NULL
- `chain_reference` TEXT
- `status` TEXT NOT NULL
- `difference_amount` TEXT
- `difference_currency` TEXT
- `summary` TEXT NOT NULL
- `recorded_at` TEXT NOT NULL
- `metadata_json` TEXT NOT NULL

#### `circuit_breaker_states`
- `circuit_breaker_id` TEXT PRIMARY KEY
- `scope` TEXT NOT NULL
- `scope_reference` TEXT NOT NULL
- `state` TEXT NOT NULL
- `reason` TEXT NOT NULL
- `triggered_by` TEXT NOT NULL
- `threshold_key` TEXT NOT NULL
- `trigger_value` REAL
- `reset_at` TEXT
- `manual_override` BOOLEAN NOT NULL
- `updated_at` TEXT NOT NULL

#### `alerts`
- `alert_id` TEXT PRIMARY KEY
- `category` TEXT NOT NULL
- `severity` TEXT NOT NULL
- `status` TEXT NOT NULL
- `title` TEXT NOT NULL
- `description` TEXT NOT NULL
- `entity_type` TEXT NOT NULL
- `entity_id` TEXT NOT NULL
- `service_name` TEXT NOT NULL
- `correlation_id` TEXT
- `created_at` TEXT NOT NULL
- `acknowledged_at` TEXT
- `metadata_json` TEXT NOT NULL

#### `investigation_cases`
- `case_id` TEXT PRIMARY KEY
- `case_type` TEXT NOT NULL
- `status` TEXT NOT NULL
- `priority` TEXT NOT NULL
- `subject_entity_type` TEXT NOT NULL
- `subject_entity_id` TEXT NOT NULL
- `opened_by` TEXT NOT NULL
- `assigned_to` TEXT
- `summary` TEXT NOT NULL
- `alert_ids_json` TEXT NOT NULL
- `decision_refs_json` TEXT NOT NULL
- `opened_at` TEXT NOT NULL
- `closed_at` TEXT

## Audit Log Schema

The audit log is append-only and must be immutable at the application layer.

### `audit_logs`
- `audit_log_id` TEXT PRIMARY KEY
- `occurred_at` TEXT NOT NULL
- `actor_type` TEXT NOT NULL
- `actor_id` TEXT NOT NULL
- `action` TEXT NOT NULL
- `entity_type` TEXT NOT NULL
- `entity_id` TEXT NOT NULL
- `correlation_id` TEXT
- `before_json` TEXT
- `after_json` TEXT
- `reason` TEXT
- `service_name` TEXT NOT NULL
- `provenance_record_id` TEXT
- `signature` TEXT

## Domain Schemas

### Risk Decision Schema

```json
{
  "riskDecisionId": "rk_123",
  "requestId": "txr_123",
  "decision": "approve|review|block",
  "aggregatedScore": 72.5,
  "maxSignalSeverity": "medium|high|critical",
  "recommendedAction": "approve|step_up_review|reject|freeze_wallet",
  "policyVersion": "risk-policy-v1",
  "reasons": ["velocity breach", "new beneficiary"],
  "signals": ["sig_1", "sig_2"],
  "createdAt": "2026-03-18T00:00:00Z"
}
```

### Oracle Consensus Schema

```json
{
  "consensusId": "ocd_123",
  "assetSymbol": "USTB",
  "decision": "accepted|stale|divergent|insufficient_sources",
  "consensusValue": 99.98,
  "allowedDeviationBps": 25,
  "observedSpreadBps": 11,
  "minimumSources": 3,
  "participatingSources": ["sourceA", "sourceB", "sourceC"],
  "rejectedSources": ["sourceD"],
  "stale": false,
  "createdAt": "2026-03-18T00:00:00Z"
}
```

### Compliance Decision Schema

```json
{
  "complianceDecisionId": "cmp_123",
  "requestId": "txr_123",
  "decision": "approve|review|block",
  "reviewRequired": true,
  "policyVersion": "compliance-v1",
  "jurisdiction": "US",
  "reasons": ["pep screening pending"],
  "sanctionsHitIds": ["hit_123"],
  "createdAt": "2026-03-18T00:00:00Z"
}
```

### Circuit Breaker State Model

```json
{
  "circuitBreakerId": "cb_123",
  "scope": "global|asset|wallet|jurisdiction|service",
  "scopeReference": "USTB",
  "state": "closed|open|half_open",
  "reason": "oracle divergence above threshold",
  "triggeredBy": "oracle-service",
  "thresholdKey": "oracle_spread_bps",
  "triggerValue": 41,
  "resetAt": "2026-03-18T00:15:00Z",
  "manualOverride": false,
  "updatedAt": "2026-03-18T00:00:00Z"
}
```

## Deployment Evolution Path

Phase 1 runs locally with a shared SQLite file, but the architecture is intentionally compatible with a later split into:

- API read/write database,
- event bus for asynchronous workflows,
- per-service datastores,
- dedicated case management and audit infrastructure,
- stronger policy and identity integrations.

The shared model package should remain the canonical interface even as storage and transport evolve.
