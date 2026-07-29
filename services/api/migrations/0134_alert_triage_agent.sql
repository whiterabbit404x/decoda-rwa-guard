-- Alert Triage Agent foundation (Screen 6 — Active Alerts).
--
-- Persists the autonomous Alert Triage Agent's output: deterministic root-cause
-- clusters over ACTIVE alerts, their derived severity / confidence / reasoning /
-- recommendation, the triage-run lifecycle, and an audit history of cluster and
-- membership changes.
--
-- Design notes / truthfulness:
--   * Every table is new and workspace-scoped; nothing here touches the live
--     telemetry -> detection -> alert -> incident path or the existing incident-level
--     AI triage tables (0123). Screen 6 clustering is a read-side grouping of alerts
--     that already exist — it never creates, resolves, or mutates an alert.
--   * A cluster is identified by a STABLE root-cause fingerprint (normalized
--     evidence, no page timestamps / display text / random values), so re-running
--     triage updates the same cluster instead of creating a duplicate.
--   * Severity / confidence are computed by the centralized backend policy and
--     stored with their contributing factors so the UI can explain the ranking.
--   * All DDL is idempotent (IF NOT EXISTS / additive) so the startup migration
--     runner can re-apply it safely.

-- ---------------------------------------------------------------------------
-- alert_clusters — one row per active root-cause cluster (stable fingerprint).
-- The recommendation lives here (deterministic category first; optional AI
-- narrative in recommendation_summary, with recommendation_source recording
-- whether an AI provider actually produced part of the result).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alert_clusters (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    -- Stable, workspace-scoped root-cause fingerprint. Cross-workspace collisions
    -- are impossible because workspace identity is folded into the hash AND the
    -- unique index below is workspace-scoped.
    fingerprint TEXT NOT NULL,
    title TEXT NOT NULL,
    derived_severity TEXT NOT NULL DEFAULT 'low'
        CHECK (derived_severity IN ('low', 'medium', 'high', 'critical')),
    severity_factors JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- 0..1 detector-style confidence. NULL means "not calculated" (never shown as 0).
    confidence NUMERIC(4, 3) NULL,
    confidence_band TEXT NULL
        CHECK (confidence_band IS NULL OR confidence_band IN ('low', 'medium', 'high')),
    confidence_factors JSONB NOT NULL DEFAULT '[]'::jsonb,
    reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    recommendation_category TEXT NULL,
    recommendation_summary TEXT NULL,
    recommendation_source TEXT NOT NULL DEFAULT 'rules_based'
        CHECK (recommendation_source IN ('rules_based', 'ai_assisted')),
    primary_asset_id UUID NULL,
    primary_asset_name TEXT NULL,
    chain_id TEXT NULL,
    detection_family TEXT NULL,
    member_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived')),
    first_seen_at TIMESTAMPTZ NULL,
    last_seen_at TIMESTAMPTZ NULL,
    last_triage_run_id UUID NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One cluster per (workspace, fingerprint): the upsert key that makes re-running
-- triage idempotent instead of duplicating clusters.
CREATE UNIQUE INDEX IF NOT EXISTS uq_alert_clusters_fingerprint
    ON alert_clusters (workspace_id, fingerprint);
CREATE INDEX IF NOT EXISTS idx_alert_clusters_workspace_status
    ON alert_clusters (workspace_id, status, created_at DESC);

-- ---------------------------------------------------------------------------
-- alert_cluster_members — membership. A single active alert belongs to at most
-- one cluster (uq on workspace_id, alert_id) and can never appear twice in the
-- same cluster (uq on workspace_id, cluster_id, alert_id).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alert_cluster_members (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    cluster_id UUID NOT NULL REFERENCES alert_clusters(id) ON DELETE CASCADE,
    alert_id UUID NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    member_severity TEXT NULL,
    triage_run_id UUID NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_alert_cluster_members_cluster_alert
    ON alert_cluster_members (workspace_id, cluster_id, alert_id);
-- Cross-cluster dedup: an alert is a member of at most one cluster at a time.
CREATE UNIQUE INDEX IF NOT EXISTS uq_alert_cluster_members_alert
    ON alert_cluster_members (workspace_id, alert_id);
CREATE INDEX IF NOT EXISTS idx_alert_cluster_members_cluster
    ON alert_cluster_members (workspace_id, cluster_id);

-- ---------------------------------------------------------------------------
-- alert_triage_runs — one row per triage attempt. mode records whether the run
-- was purely rules-based, AI-assisted (an AI provider produced part of it), or
-- unavailable. A partial index enforces "one active run per workspace" so the
-- Run Triage button can truthfully disable while a run is in progress.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alert_triage_runs (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'completed', 'failed')),
    mode TEXT NOT NULL DEFAULT 'rules_based'
        CHECK (mode IN ('rules_based', 'ai_assisted', 'unavailable')),
    triggered_by TEXT NOT NULL DEFAULT 'system'
        CHECK (triggered_by IN ('user', 'system')),
    triggered_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    alerts_considered INTEGER NOT NULL DEFAULT 0,
    clusters_total INTEGER NOT NULL DEFAULT 0,
    clusters_created INTEGER NOT NULL DEFAULT 0,
    clusters_updated INTEGER NOT NULL DEFAULT 0,
    unclustered_count INTEGER NOT NULL DEFAULT 0,
    ai_enriched_count INTEGER NOT NULL DEFAULT 0,
    error_code TEXT NULL,
    error_detail TEXT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alert_triage_runs_workspace
    ON alert_triage_runs (workspace_id, created_at DESC);
-- At most one in-flight run per workspace (idempotency / "processing" guard).
CREATE UNIQUE INDEX IF NOT EXISTS uq_alert_triage_runs_active
    ON alert_triage_runs (workspace_id)
    WHERE status = 'running';

-- ---------------------------------------------------------------------------
-- alert_cluster_history — audit trail of cluster identity / membership /
-- severity / recommendation changes. cluster_id is intentionally NOT a foreign
-- key so history survives cluster archival and is preserved across membership
-- and severity changes (an append-only record of what changed and why).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alert_cluster_history (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    cluster_id UUID NULL,
    triage_run_id UUID NULL,
    change_type TEXT NOT NULL
        CHECK (change_type IN (
            'triage_run_started', 'triage_run_completed', 'triage_run_failed',
            'cluster_created', 'membership_changed', 'severity_recalculated',
            'recommendation_changed', 'cluster_archived', 'manual_cluster_review',
            'manual_override'
        )),
    actor TEXT NOT NULL DEFAULT 'system',
    actor_user_id UUID NULL,
    old_value JSONB NULL,
    new_value JSONB NULL,
    reason TEXT NULL,
    alert_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alert_cluster_history_workspace
    ON alert_cluster_history (workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_cluster_history_cluster
    ON alert_cluster_history (workspace_id, cluster_id, created_at DESC);
