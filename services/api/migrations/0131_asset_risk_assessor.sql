-- Asset Risk Assessor (Screen 3 — Protected Asset Registry).
--
-- Adds the persistence backing the autonomous, deterministic Asset Risk
-- Assessor: reserve-backing configuration on the asset, a history of valuation
-- and reserve-verification observations (so market deviation and feed freshness
-- are computed from evidence, never invented), timestamped risk-assessment
-- snapshots, deduplicated risk findings, and the job/lease table that keeps the
-- worker idempotent and free of duplicate concurrent assessments.
--
-- All DDL is idempotent (IF NOT EXISTS / additive, NULL-tolerant ALTERs) so the
-- startup migration runner can re-apply it safely. Every table is new and
-- workspace-scoped; nothing here touches the live telemetry -> detection ->
-- alert -> incident path. Financial values use NUMERIC (never floating point).

-- ---------------------------------------------------------------------------
-- Registry + reserve-backing configuration on the canonical assets table.
--   * rwa_asset_type   — customer-facing RWA product taxonomy shown in the
--                        registry ("Asset Type" column). The existing
--                        asset_type stays the technical monitoring taxonomy.
--   * custodian        — off-chain custodian / issuer of record.
--   * value_usd        — headline protected value (the create form already
--                        collects it; it previously had nowhere to land).
--   * reserve_*        — configuration for reserve verification. reserve_value_usd
--                        / reserve_verified_at hold the last attested off-chain
--                        reserve observation for manual / attestation feeds. The
--                        raw feed secret is NEVER stored here — only a
--                        non-sensitive identifier/label.
-- ---------------------------------------------------------------------------
ALTER TABLE assets
    ADD COLUMN IF NOT EXISTS rwa_asset_type TEXT NULL,
    ADD COLUMN IF NOT EXISTS custodian TEXT NULL,
    ADD COLUMN IF NOT EXISTS value_usd NUMERIC(20, 2) NULL,
    ADD COLUMN IF NOT EXISTS token_symbol TEXT NULL,
    ADD COLUMN IF NOT EXISTS price_source TEXT NULL,
    ADD COLUMN IF NOT EXISTS reserve_feed_type TEXT NOT NULL DEFAULT 'none',
    ADD COLUMN IF NOT EXISTS reserve_feed_identifier TEXT NULL,
    ADD COLUMN IF NOT EXISTS reserve_min_coverage_ratio NUMERIC(10, 4) NOT NULL DEFAULT 1.0,
    ADD COLUMN IF NOT EXISTS reserve_update_interval_seconds INTEGER NULL,
    ADD COLUMN IF NOT EXISTS reserve_value_usd NUMERIC(20, 2) NULL,
    ADD COLUMN IF NOT EXISTS reserve_verified_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS circulating_supply NUMERIC(78, 0) NULL,
    ADD COLUMN IF NOT EXISTS reference_price_usd NUMERIC(38, 18) NULL;

CREATE INDEX IF NOT EXISTS idx_assets_workspace_rwa_type
    ON assets (workspace_id, rwa_asset_type) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_assets_workspace_custodian
    ON assets (workspace_id, custodian) WHERE deleted_at IS NULL;

-- ---------------------------------------------------------------------------
-- Valuation snapshots — the rolling history the market-deviation detector reads.
-- One row per observed valuation (worker cycle or manual). A robust baseline is
-- computed from these; a new asset with too few rows is "baseline learning",
-- never flagged anomalous.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_valuation_snapshots (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    price_usd NUMERIC(38, 18) NULL,
    market_value_usd NUMERIC(20, 2) NULL,
    circulating_supply NUMERIC(78, 0) NULL,
    source TEXT NOT NULL DEFAULT 'unknown',
    is_estimated BOOLEAN NOT NULL DEFAULT FALSE,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_asset_valuation_snapshots_asset_observed
    ON asset_valuation_snapshots (workspace_id, asset_id, observed_at DESC);

-- ---------------------------------------------------------------------------
-- Reserve verification snapshots — evidence that a reserve value was observed
-- from a configured feed at a point in time. verified=false means the value is
-- unverified/estimated and must not be presented as customer evidence.
-- feed_identifier_hash stores a SHA-256 of the feed identifier, never the raw
-- endpoint/secret.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_reserve_snapshots (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    reserve_value_usd NUMERIC(20, 2) NULL,
    liability_value_usd NUMERIC(20, 2) NULL,
    coverage_ratio NUMERIC(14, 6) NULL,
    feed_type TEXT NOT NULL DEFAULT 'none',
    feed_identifier_hash TEXT NULL,
    source TEXT NOT NULL DEFAULT 'unknown',
    verified BOOLEAN NOT NULL DEFAULT FALSE,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_asset_reserve_snapshots_asset_observed
    ON asset_reserve_snapshots (workspace_id, asset_id, observed_at DESC);

-- ---------------------------------------------------------------------------
-- Risk assessment snapshots — one immutable row per assessment run. Preserves
-- the deterministic inputs, the canonical weighted score, dimension breakdown,
-- reserve/liability/coverage, price-deviation metrics, feed freshness,
-- monitoring coverage, findings summary, evidence references, and the
-- (AI-or-deterministic) narrative. The frontend never recomputes the score.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_risk_assessments (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    risk_score INTEGER NOT NULL DEFAULT 0,
    risk_level TEXT NOT NULL DEFAULT 'low',
    confidence NUMERIC(4, 3) NOT NULL DEFAULT 0,
    score_version TEXT NOT NULL DEFAULT 'asset-risk-v1',
    dimensions JSONB NOT NULL DEFAULT '[]'::jsonb,
    reserve_value_usd NUMERIC(20, 2) NULL,
    liability_value_usd NUMERIC(20, 2) NULL,
    reserve_coverage_percent NUMERIC(14, 4) NULL,
    reserve_difference_usd NUMERIC(20, 2) NULL,
    reserve_status TEXT NOT NULL DEFAULT 'insufficient_evidence',
    price_deviation_7d_percent NUMERIC(14, 4) NULL,
    price_deviation_30d_percent NUMERIC(14, 4) NULL,
    price_zscore NUMERIC(14, 4) NULL,
    feed_freshness JSONB NOT NULL DEFAULT '{}'::jsonb,
    monitoring_coverage_percent NUMERIC(6, 2) NULL,
    monitoring_health TEXT NOT NULL DEFAULT 'unknown',
    data_completeness NUMERIC(4, 3) NOT NULL DEFAULT 0,
    findings JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    ai_summary TEXT NULL,
    ai_summary_source TEXT NOT NULL DEFAULT 'deterministic',
    status TEXT NOT NULL DEFAULT 'completed',
    trigger_source TEXT NOT NULL DEFAULT 'worker',
    assessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_asset_risk_assessments_asset_assessed
    ON asset_risk_assessments (workspace_id, asset_id, assessed_at DESC);
CREATE INDEX IF NOT EXISTS idx_asset_risk_assessments_workspace_assessed
    ON asset_risk_assessments (workspace_id, assessed_at DESC);

-- ---------------------------------------------------------------------------
-- Risk findings — deduplicated by a stable fingerprint per (workspace, asset,
-- finding_type + salient evidence). A finding is updated (last_seen_at,
-- occurrence_count) while the condition persists, and resolved when it clears.
-- alert_id links the finding to the alert it raised so the two lifecycles stay
-- in sync.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_risk_findings (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    assessment_id UUID NULL REFERENCES asset_risk_assessments(id) ON DELETE SET NULL,
    finding_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'medium',
    status TEXT NOT NULL DEFAULT 'active',
    fingerprint TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    alert_id UUID NULL REFERENCES alerts(id) ON DELETE SET NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, asset_id, fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_asset_risk_findings_asset_status
    ON asset_risk_findings (workspace_id, asset_id, status, severity);
CREATE INDEX IF NOT EXISTS idx_asset_risk_findings_workspace_active
    ON asset_risk_findings (workspace_id, status) WHERE status = 'active';

-- ---------------------------------------------------------------------------
-- Assessment job / lease table. Keeps the worker idempotent: at most one active
-- (queued/running) job per asset via the partial unique index, and a lease so
-- multiple worker replicas never assess the same asset concurrently.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_risk_jobs (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    trigger_source TEXT NOT NULL DEFAULT 'worker',
    lease_owner TEXT NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    last_error TEXT NULL,
    requested_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ NULL
);

-- One active job per asset (queued or running). Explicit regeneration is allowed
-- once the prior job reaches a terminal state.
CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_risk_jobs_active_per_asset
    ON asset_risk_jobs (workspace_id, asset_id)
    WHERE status IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS idx_asset_risk_jobs_workspace_status
    ON asset_risk_jobs (workspace_id, status, created_at);
