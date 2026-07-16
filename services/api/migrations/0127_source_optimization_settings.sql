-- Source Optimization Agent (Screen 4) persistence.
--
-- Adds durable, workspace-scoped storage for:
--   * the persisted Auto-Routing setting + failover policy + threshold overrides
--     that the deterministic health engine (monitoring_health_engine.py) reads,
--   * an evidence-backed agent-decision log that records every autonomous or
--     approval-required routing/health decision with the exact input metric
--     snapshot used, so no decision is ever shown without supporting evidence.
--
-- All DDL is idempotent (IF NOT EXISTS / additive) so the startup migration
-- runner can re-apply it safely. Nothing here modifies an existing table. Redis
-- remains the live SSE fan-out only; Postgres is the source of truth.

-- ---------------------------------------------------------------------------
-- Persisted per-workspace Auto-Routing + failover policy + threshold overrides.
-- One row per workspace. Absence of a row means "Auto-Routing disabled, spec
-- default thresholds" (fail-closed).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workspace_source_settings (
    workspace_id UUID PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    auto_routing_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    failover_cooldown_seconds INTEGER NOT NULL DEFAULT 300,
    route_recovery_seconds INTEGER NOT NULL DEFAULT 900,
    thresholds JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Evidence-backed agent decision log. Every autonomous decision, escalation and
-- approval-required recommendation the Source Optimization Agent produces is
-- recorded here with the input metric snapshot it was derived from. This is the
-- backing store for the Agent Activity card and Recent Agent Decisions panel.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_agent_decisions (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    target_id UUID NULL,          -- legacy targets(id) or canonical monitored_targets(id); NULL for workspace-level
    system_id UUID NULL,
    provider_id TEXT NULL,
    decision_type TEXT NOT NULL
        CHECK (decision_type IN (
            'no_action', 'health_check_completed', 'warning_opened',
            'failover_recommended', 'failover_initiated', 'failover_completed',
            'failover_failed', 'route_restored', 'escalation_created',
            'provider_marked_unhealthy', 'oracle_heartbeat_warning'
        )),
    triggered_rule TEXT NULL,
    status TEXT NOT NULL DEFAULT 'recorded'
        CHECK (status IN ('recorded', 'pending_approval', 'approved', 'dismissed', 'executed', 'failed')),
    approval_required BOOLEAN NOT NULL DEFAULT FALSE,
    confidence TEXT NULL,
    health_status TEXT NULL,
    health_score NUMERIC NULL,
    previous_route TEXT NULL,
    new_route TEXT NULL,
    input_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,   -- exact metrics used for this decision
    execution_result JSONB NULL,
    rollback_result JSONB NULL,
    correlation_id TEXT NULL,
    actor_type TEXT NOT NULL DEFAULT 'agent'
        CHECK (actor_type IN ('agent', 'user', 'system')),
    actor_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    software_version TEXT NULL,     -- commit SHA when available
    summary TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    executed_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_source_agent_decisions_workspace_created_desc
    ON source_agent_decisions (workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_source_agent_decisions_workspace_status
    ON source_agent_decisions (workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_source_agent_decisions_workspace_target
    ON source_agent_decisions (workspace_id, target_id);
CREATE INDEX IF NOT EXISTS idx_source_agent_decisions_workspace_type_created
    ON source_agent_decisions (workspace_id, decision_type, created_at DESC);
