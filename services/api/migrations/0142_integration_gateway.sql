-- Migration 0142: Integration Gateway (Screen 10) persistence.
--
-- Screen 10 becomes the operational control plane for Decoda's external
-- dependencies (blockchain RPC / oracle / custody / stablecoin / security
-- providers, outbound webhooks and API credentials). This migration adds the
-- ONLY two new tables the control plane needs; everything else is derived from
-- CANONICAL sources that already exist and must NOT be duplicated:
--
--   * provider health           -> provider_health_records (migrations 0076/0082),
--                                  aggregated by pilot._build_monitoring_sources_enrichment
--                                  (the same source Screen 4 + Screen 12 read).
--   * SaaS-infra integrations    -> pilot.integration_health_snapshot (env-derived).
--   * webhook delivery health    -> workspace_webhooks + webhook_deliveries (migration 0005).
--   * routing / failover config  -> workspace_source_settings (migration 0127).
--   * approvals                  -> response_action_approvals (migration 0137).
--   * audit                      -> audit_logs (hash-chained; pilot.log_audit).
--
-- The two new tables are:
--   1. integration_health_scans        — one row per Run Health Check execution.
--        This is the canonical source for "Last scan: 3m ago" so a browser refresh
--        (GET) preserves the SAME timestamp until another scan runs (spec §15/§24).
--   2. integration_recommendations     — the Integration Gateway Agent's structured,
--        evidence-backed recommendations. Deterministic detectors are the source of
--        truth; the AI layer only summarises/prioritises. Recommendations are
--        idempotent: at most ONE open (unresolved) recommendation per
--        (workspace, dedupe_key), enforced by a partial UNIQUE index so a repeated
--        scan updates the existing row instead of stacking REC-001..REC-00N (spec §53).
--
-- All DDL is additive and idempotent (IF NOT EXISTS) so the startup migration
-- runner can re-apply it safely. Nothing here modifies or drops an existing table.
-- No raw secret is ever stored here — only safe metadata (spec §9).

-- ---------------------------------------------------------------------------
-- 1. Integration health scans — the canonical "last scan" record.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS integration_health_scans (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    -- 'completed' once the scan finished (even with partial provider failures —
    -- failure isolation, spec §52); 'failed' only if the whole run aborted.
    status TEXT NOT NULL DEFAULT 'completed'
        CHECK (status IN ('running', 'completed', 'failed')),
    -- Who/what initiated it. A browser refresh never creates a scan (GET-only).
    trigger TEXT NOT NULL DEFAULT 'manual'
        CHECK (trigger IN ('manual', 'scheduled', 'worker')),
    -- Aggregate provider counts observed by this scan (measured, never invented).
    checked_count INTEGER NOT NULL DEFAULT 0,
    healthy_count INTEGER NOT NULL DEFAULT 0,
    degraded_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    -- Open recommendation count + derived connection risk at scan completion.
    recommendations_open INTEGER NOT NULL DEFAULT 0,
    connection_risk TEXT NULL
        CHECK (connection_risk IS NULL OR connection_risk IN ('low', 'medium', 'high', 'critical', 'unknown')),
    correlation_id TEXT NULL,               -- ties the scan to its diagnostic + audit rows
    summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_integration_health_scans_workspace_started_desc
    ON integration_health_scans (workspace_id, started_at DESC);

-- ---------------------------------------------------------------------------
-- 2. Integration Gateway recommendations — structured, evidence-backed, idempotent.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS integration_recommendations (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    -- Stable identifier of the integration this recommendation is about, e.g.
    -- 'provider:alchemy', 'webhook:<uuid>', 'dependency:base-rpc'. Part of the
    -- dedupe key so a per-provider finding collapses to one open row.
    integration_key TEXT NOT NULL,
    integration_kind TEXT NOT NULL DEFAULT 'provider'
        CHECK (integration_kind IN ('provider', 'api', 'webhook', 'dependency', 'workspace')),
    recommendation_type TEXT NOT NULL
        CHECK (recommendation_type IN (
            'rotate_credential', 'narrow_permissions', 'enable_failover',
            'add_fallback_provider', 'switch_provider', 'disable_unused_integration',
            'review_webhook', 'adjust_retry_policy', 'investigate_auth_failures',
            'investigate_provider_degradation', 'run_health_check', 'other'
        )),
    severity TEXT NOT NULL DEFAULT 'medium'
        CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    title TEXT NOT NULL,
    description TEXT NULL,
    reason TEXT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,   -- safe, machine-verifiable facts only
    recommended_action TEXT NULL,
    -- Qualitative confidence only — never a fabricated percentage (spec §32).
    confidence TEXT NULL
        CHECK (confidence IS NULL OR confidence IN ('high', 'medium', 'low')),
    risk_if_ignored TEXT NULL,
    -- Autonomy level (spec §18): 'auto' = safe automatic; 'approval_required' =
    -- routes through the canonical Screen 8 approval flow; 'manual' = operator
    -- must act externally (e.g. rotate an external key we cannot rotate); 'advisory'.
    execution_mode TEXT NOT NULL DEFAULT 'advisory'
        CHECK (execution_mode IN ('auto', 'approval_required', 'manual', 'advisory')),
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN (
            'open', 'acknowledged', 'approval_required', 'approved',
            'executing', 'executed', 'failed', 'dismissed', 'obsolete'
        )),
    -- Canonical dedupe key: (recommendation_type + integration_key). A repeated
    -- scan that re-detects the same condition UPDATEs the open row instead of
    -- inserting a duplicate; when the condition clears the row is resolved
    -- (status -> obsolete, resolved_at set) so a later recurrence starts fresh.
    dedupe_key TEXT NOT NULL,
    correlation_id TEXT NULL,               -- the scan run that created/last-refreshed it
    detected_count INTEGER NOT NULL DEFAULT 1,   -- how many scans have re-observed it
    acknowledged_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    acknowledged_at TIMESTAMPTZ NULL,
    resolved_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    resolved_at TIMESTAMPTZ NULL,           -- NULL == open/unresolved (drives the partial unique)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Idempotency guarantee (spec §53): at most ONE unresolved recommendation per
-- (workspace, dedupe_key). resolved_at IS NULL is the "open" predicate, so a
-- dismissed/obsolete/executed row is retained for history and never blocks a
-- genuine later recurrence. The application upserts against this exact predicate.
-- NOTE: the partial predicate below is matched verbatim by the Python upsert so
-- an ON CONFLICT arbiter (if ever used) resolves to this index (spec §21 warning).
CREATE UNIQUE INDEX IF NOT EXISTS uq_integration_recommendations_open
    ON integration_recommendations (workspace_id, dedupe_key)
    WHERE resolved_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_integration_recommendations_workspace_status_created
    ON integration_recommendations (workspace_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_integration_recommendations_workspace_integration
    ON integration_recommendations (workspace_id, integration_key);
