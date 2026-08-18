-- Screen 11 Governance Guard.
--
-- Adds the minimal canonical state Screen 11 genuinely requires:
--   1. workspace_settings          -- general workspace config (timezone/currency)
--                                     with an optimistic-concurrency version.
--   2. governance_change_requests  -- the approval workflow for HIGH/CRITICAL
--                                     configuration changes (separation of duties).
--   3. governance_anomalies        -- deterministic access-anomaly findings.
--
-- It deliberately creates NO parallel audit or MFA state. Configuration
-- accountability continues to flow through the canonical hash-chained,
-- append-only ``audit_logs`` (migrations 0001/0091/0100) via ``log_audit()``, and
-- the canonical MFA/session policy remains ``workspace_auth_policies``
-- (migration 0092). This migration is additive and idempotent (IF NOT EXISTS),
-- so the startup migration runner can re-apply it safely.

-- 1) General workspace settings ------------------------------------------------
-- Workspace NAME stays canonical in workspaces.name; timezone/currency live here.
-- ``version`` guards the WHOLE General form (name + timezone + currency): a save
-- carries the version it was loaded with, and a stale browser tab submitting an
-- old version is rejected with a conflict instead of silently overwriting a newer
-- configuration.
-- ``governance_last_evaluated_at`` records when the access-anomaly engine last
-- ran for this workspace so posture can distinguish "evaluated, 0 findings" from
-- "never evaluated" (fail-closed — missing evidence is never rendered as safe).
CREATE TABLE IF NOT EXISTS workspace_settings (
    workspace_id UUID PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    currency TEXT NOT NULL DEFAULT 'USD',
    version INTEGER NOT NULL DEFAULT 1,
    governance_last_evaluated_at TIMESTAMPTZ NULL,
    updated_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2) Configuration-change approval workflow -----------------------------------
-- Only changes that policy requires approval for (HIGH/CRITICAL) are persisted
-- here. LOW/MEDIUM changes apply directly and are recorded in audit_logs. The
-- canonical target state (e.g. workspace_auth_policies) is NEVER mutated while a
-- request is ``pending`` — it is applied atomically only on execution after an
-- authorized, separate-duty approval.
CREATE TABLE IF NOT EXISTS governance_change_requests (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    requested_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    requested_by_role TEXT NULL,
    change_type TEXT NOT NULL,
    target TEXT NOT NULL,
    before_value JSONB NOT NULL DEFAULT '{}'::jsonb,
    proposed_value JSONB NOT NULL DEFAULT '{}'::jsonb,
    risk_level TEXT NOT NULL CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    risk_reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'executing', 'completed', 'failed', 'cancelled')),
    decision_note TEXT NULL,
    approved_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at TIMESTAMPTZ NULL,
    executed_at TIMESTAMPTZ NULL,
    failure_reason TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_governance_change_requests_workspace_status
    ON governance_change_requests (workspace_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_governance_change_requests_workspace_created
    ON governance_change_requests (workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_governance_change_requests_workspace_requester
    ON governance_change_requests (workspace_id, requested_by_user_id);

-- At most one pending request per (workspace, change_type, target) so a stale tab
-- or a rapid double-submit cannot stack duplicate pending approvals for the same
-- setting.
CREATE UNIQUE INDEX IF NOT EXISTS uq_governance_change_requests_pending_target
    ON governance_change_requests (workspace_id, change_type, target)
    WHERE status = 'pending';

-- 3) Access-anomaly findings ---------------------------------------------------
-- Produced ONLY by explicit deterministic evaluation (never as a side effect of a
-- GET). Evidence is JSONB drawn from the workspace's own audit_logs.
CREATE TABLE IF NOT EXISTS governance_anomalies (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    confidence NUMERIC(5, 2) NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'acknowledged', 'resolved', 'dismissed')),
    dedup_key TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ NULL,
    resolved_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_governance_anomalies_workspace_status
    ON governance_anomalies (workspace_id, status, severity);
CREATE INDEX IF NOT EXISTS idx_governance_anomalies_workspace_user
    ON governance_anomalies (workspace_id, user_id);
CREATE INDEX IF NOT EXISTS idx_governance_anomalies_workspace_created
    ON governance_anomalies (workspace_id, created_at DESC);

-- Dedup so re-running evaluation updates an existing ACTIVE finding (open or
-- acknowledged) in place instead of creating duplicates. A resolved/dismissed
-- finding for the same signal does not block a fresh active finding later.
CREATE UNIQUE INDEX IF NOT EXISTS uq_governance_anomalies_active_dedup
    ON governance_anomalies (workspace_id, dedup_key)
    WHERE status IN ('open', 'acknowledged');
