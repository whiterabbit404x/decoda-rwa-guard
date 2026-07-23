-- Dashboard / Executive Summary (Screen 2) persistence.
--
-- Adds two new, workspace-scoped tables backing the Dashboard Co-Pilot:
--
--   * dashboard_snapshots       — periodic point-in-time captures of the
--     deterministic risk + health scores and headline metrics. These power the
--     seven-day Risk Trend chart and the snapshot-over-snapshot deltas. One row
--     per capture (on the monitoring schedule / a sensible interval), never one
--     per page request.
--
--   * dashboard_executive_briefs — the AI-or-deterministic Executive Brief,
--     generated at most once per workspace per reporting period. The
--     idempotency key (workspace + reporting date + brief version + prompt
--     version) makes generation safely repeatable.
--
-- All DDL is idempotent (IF NOT EXISTS / additive ALTERs) so the startup
-- migration runner can re-apply it safely. Both tables are new and every query
-- against them is workspace-scoped; nothing here touches the live
-- telemetry -> detection -> alert -> incident path.

-- ---------------------------------------------------------------------------
-- Periodic dashboard snapshots (risk + health trend + deltas)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dashboard_snapshots (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    risk_score INTEGER NOT NULL DEFAULT 0,
    risk_band TEXT NOT NULL DEFAULT 'low',
    health_score INTEGER NOT NULL DEFAULT 0,
    health_status TEXT NOT NULL DEFAULT 'not_configured',
    active_alert_count INTEGER NOT NULL DEFAULT 0,
    open_incident_count INTEGER NOT NULL DEFAULT 0,
    monitored_asset_count INTEGER NOT NULL DEFAULT 0,
    active_monitor_count INTEGER NOT NULL DEFAULT 0,
    data_source_count INTEGER NOT NULL DEFAULT 0,
    uptime_30d_percent NUMERIC(6, 3) NULL,
    total_asset_value_usd NUMERIC(20, 2) NULL,
    risk_components JSONB NOT NULL DEFAULT '[]'::jsonb,
    health_components JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Defensive for a pre-existing table missing the newest columns.
ALTER TABLE dashboard_snapshots
    ADD COLUMN IF NOT EXISTS uptime_30d_percent NUMERIC(6, 3) NULL,
    ADD COLUMN IF NOT EXISTS total_asset_value_usd NUMERIC(20, 2) NULL,
    ADD COLUMN IF NOT EXISTS risk_components JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS health_components JSONB NOT NULL DEFAULT '[]'::jsonb;

-- Trend + latest-snapshot lookups are always workspace-scoped, newest first.
CREATE INDEX IF NOT EXISTS idx_dashboard_snapshots_workspace_captured
    ON dashboard_snapshots (workspace_id, captured_at DESC);

-- ---------------------------------------------------------------------------
-- Executive briefs (one per workspace per reporting period)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dashboard_executive_briefs (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL,
    reporting_date DATE NOT NULL,
    period_start TIMESTAMPTZ NULL,
    period_end TIMESTAMPTZ NULL,
    headline TEXT NOT NULL,
    summary TEXT NOT NULL,
    key_findings JSONB NOT NULL DEFAULT '[]'::jsonb,
    recommended_focus JSONB NOT NULL DEFAULT '[]'::jsonb,
    citations JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence NUMERIC(4, 3) NOT NULL DEFAULT 0,
    generation_mode TEXT NOT NULL DEFAULT 'deterministic_fallback'
        CHECK (generation_mode IN ('ai', 'deterministic_fallback')),
    provider TEXT NULL,
    model TEXT NULL,
    prompt_version TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One brief per workspace per idempotency key. Regeneration for a new prompt
-- version / brief version yields a distinct key, so history is preserved while
-- same-period re-requests are collapsed onto the existing row.
CREATE UNIQUE INDEX IF NOT EXISTS uq_dashboard_briefs_workspace_key
    ON dashboard_executive_briefs (workspace_id, idempotency_key);

CREATE INDEX IF NOT EXISTS idx_dashboard_briefs_workspace_reporting
    ON dashboard_executive_briefs (workspace_id, reporting_date DESC);
