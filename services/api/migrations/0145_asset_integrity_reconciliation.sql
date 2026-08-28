-- Asset Integrity / Reconciliation (Screen 3 — Integrity tab).
--
-- Adds the persistence behind the deterministic RWA operational-integrity layer:
-- the blockchain OBSERVATION of an asset's supply, the AUTHORITATIVE off-chain
-- business state it is reconciled against, the authorized issuance/redemption
-- records the matcher searches, and the immutable reconciliation snapshots the
-- UI reads.
--
-- Trust boundary encoded by this schema:
--   observation (on-chain)  +  authoritative state (off-chain business record)
--     -> deterministic matcher -> reconciliation snapshot (status + reason code)
-- The AI layer never writes to any table here.
--
-- All DDL is idempotent (IF NOT EXISTS / additive) so the startup migration
-- runner can re-apply it safely. Every table is workspace-scoped and cascades
-- from workspaces/assets. Supply values use NUMERIC(78, 0) base units (uint256
-- range, never floating point); USD values use NUMERIC(20, 2).
--
-- Nothing here changes the existing telemetry -> detection -> alert -> incident
-- path; reconciliation emits its canonical operational-integrity event into the
-- EXISTING threat_detections table (Screen 5) rather than a parallel one.

-- ---------------------------------------------------------------------------
-- asset_onchain_supply_observations — what the chain actually said.
--   One row per observed total-supply reading. block_number / tx_hash /
--   provider_type record provenance; evidence_source keeps simulator and replay
--   data from ever being presented as live customer evidence. Vendor-independent:
--   provider_type is provenance only, never business logic.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_onchain_supply_observations (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    total_supply NUMERIC(78, 0) NULL,
    token_decimals INTEGER NULL,
    chain_network TEXT NULL,
    contract_address TEXT NULL,
    block_number BIGINT NULL,
    tx_hash TEXT NULL,
    last_delta NUMERIC(78, 0) NULL,
    last_delta_operation TEXT NULL
        CHECK (last_delta_operation IS NULL OR last_delta_operation IN ('mint', 'burn')),
    last_delta_at TIMESTAMPTZ NULL,
    provider_type TEXT NOT NULL DEFAULT 'unknown',
    evidence_source TEXT NOT NULL DEFAULT 'live'
        CHECK (evidence_source IN ('live', 'simulator', 'replay')),
    telemetry_event_id UUID NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_asset_onchain_supply_asset_observed
    ON asset_onchain_supply_observations (workspace_id, asset_id, observed_at DESC);

-- ---------------------------------------------------------------------------
-- asset_authoritative_state — the expected operational/business state, as
--   reported by the authoritative off-chain system of record (transfer agent,
--   registrar, custodian ledger).
--   source_status distinguishes a REPORTED value from an UNAVAILABLE source, so
--   a failed upstream can never be mistaken for a variance (fail-closed).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_authoritative_state (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    expected_total_supply NUMERIC(78, 0) NULL,
    token_decimals INTEGER NULL,
    settlement_state TEXT NULL,
    source_name TEXT NOT NULL DEFAULT 'unknown',
    source_kind TEXT NOT NULL DEFAULT 'transfer_agent',
    source_status TEXT NOT NULL DEFAULT 'reported'
        CHECK (source_status IN ('reported', 'unavailable', 'error')),
    source_error TEXT NULL,
    external_reference TEXT NULL,
    evidence_source TEXT NOT NULL DEFAULT 'live'
        CHECK (evidence_source IN ('live', 'simulator', 'replay')),
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_asset_authoritative_state_asset_observed
    ON asset_authoritative_state (workspace_id, asset_id, observed_at DESC);

-- ---------------------------------------------------------------------------
-- asset_authorized_issuances — authorized issuance / redemption records from the
--   authoritative source. The deterministic matcher searches these for a record
--   that explains an observed on-chain mint/burn. A cryptographically valid
--   transaction with no matching row here is operationally UNAUTHORIZED.
--   external_reference is the business reference (subscription id, etc.).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_authorized_issuances (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    operation TEXT NOT NULL CHECK (operation IN ('mint', 'burn')),
    amount NUMERIC(78, 0) NOT NULL,
    token_decimals INTEGER NULL,
    settlement_state TEXT NOT NULL DEFAULT 'pending',
    external_reference TEXT NULL,
    source_name TEXT NOT NULL DEFAULT 'unknown',
    evidence_source TEXT NOT NULL DEFAULT 'live'
        CHECK (evidence_source IN ('live', 'simulator', 'replay')),
    authorized_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    effective_from TIMESTAMPTZ NULL,
    effective_until TIMESTAMPTZ NULL,
    consumed_by_tx_hash TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_asset_authorized_issuances_asset_authorized
    ON asset_authorized_issuances (workspace_id, asset_id, authorized_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_authorized_issuances_reference
    ON asset_authorized_issuances (workspace_id, asset_id, external_reference)
    WHERE external_reference IS NOT NULL;

-- ---------------------------------------------------------------------------
-- asset_reconciliation_snapshots — one IMMUTABLE row per reconciliation run.
--   Retains the exact inputs, the deterministic status/reason code, and the
--   rule_id/rule_version that produced them, so an auditor can reproduce the
--   verdict. Historical snapshots are never recalculated with a newer rule.
--   canonical_event_id links to the operational-integrity event in
--   threat_detections (Screen 5) when one was emitted.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_reconciliation_snapshots (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    observed_supply NUMERIC(78, 0) NULL,
    expected_supply NUMERIC(78, 0) NULL,
    variance_units NUMERIC(78, 0) NULL,
    token_decimals INTEGER NULL,
    status TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'low'
        CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    rule_id TEXT NOT NULL,
    rule_version INTEGER NOT NULL,
    rule_config JSONB NOT NULL DEFAULT '{}'::jsonb,
    onchain_observed_at TIMESTAMPTZ NULL,
    authoritative_observed_at TIMESTAMPTZ NULL,
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    onchain_source TEXT NULL,
    authoritative_source TEXT NULL,
    evidence_source TEXT NOT NULL DEFAULT 'live'
        CHECK (evidence_source IN ('live', 'simulator', 'replay')),
    block_number BIGINT NULL,
    tx_hash TEXT NULL,
    external_reference TEXT NULL,
    matched_issuance_id UUID NULL REFERENCES asset_authorized_issuances(id) ON DELETE SET NULL,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    match_detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    canonical_event_id UUID NULL REFERENCES threat_detections(id) ON DELETE SET NULL,
    ai_summary TEXT NULL,
    ai_summary_source TEXT NOT NULL DEFAULT 'deterministic',
    trigger_source TEXT NOT NULL DEFAULT 'worker',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_asset_reconciliation_snapshots_asset_evaluated
    ON asset_reconciliation_snapshots (workspace_id, asset_id, evaluated_at DESC);
CREATE INDEX IF NOT EXISTS idx_asset_reconciliation_snapshots_workspace_status
    ON asset_reconciliation_snapshots (workspace_id, status, evaluated_at DESC);
