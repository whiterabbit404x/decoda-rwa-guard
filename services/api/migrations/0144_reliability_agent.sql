-- Screen 12 Self-Healing Reliability Agent.
--
-- Adds the minimal canonical state Screen 12 genuinely requires to run the
-- reliability decision flow (OBSERVE -> CORRELATE -> CLASSIFY -> ROOT CAUSE ->
-- POLICY -> EXECUTE/RECOMMEND -> VERIFY -> AUDIT) with a durable, auditable
-- history:
--
--   1. reliability_events         -- append-mostly ledger of findings,
--                                    remediation attempts and verification
--                                    outcomes produced by the agent.
--   2. reliability_health_samples -- persisted infra-health samples used to
--                                    compute TRUTHFUL Uptime / Avg Response /
--                                    Error Rate (never invented percentages).
--
-- Design notes / truthfulness:
--   * These tables describe DECODA'S OWN shared infrastructure, so a row may be
--     PLATFORM-scoped (workspace_id NULL) rather than tenant data. They carry NO
--     customer telemetry or evidence and therefore cannot make live customer
--     monitoring appear healthy. Accountability for who triggered a remediation
--     stays in the canonical hash-chained ``audit_logs`` via ``log_audit()`` —
--     this migration deliberately creates NO parallel audit system.
--   * Health status is NEVER invented here: the canonical evaluator remains
--     ``system_health.build_system_health_snapshot`` (components + freshness).
--     This ledger only records what that evaluator observed and what the agent
--     did about it.
--   * Additive + idempotent (IF NOT EXISTS) so the startup migration runner can
--     re-apply it safely.

-- 1) Reliability event ledger --------------------------------------------------
-- One row per agent event. A ``finding`` is created when a monitoring gap /
-- degradation is detected; a ``remediation`` row (linked via parent_event_id)
-- records an attempted recovery action; a ``verification`` row records the
-- post-action health re-check. Execution success and RECOVERY success are kept
-- distinct: action_result records whether execution returned, while status only
-- advances to ``recovered`` after verification confirms health returned.
CREATE TABLE IF NOT EXISTS reliability_events (
    id UUID PRIMARY KEY,
    -- NULL = platform-scoped infrastructure event (Decoda itself). Non-null =
    -- observed within a specific workspace context.
    workspace_id UUID NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    parent_event_id UUID NULL REFERENCES reliability_events(id) ON DELETE SET NULL,
    component TEXT NOT NULL,
    event_type TEXT NOT NULL
        CHECK (event_type IN ('finding', 'remediation', 'verification', 'diagnostic')),
    -- Deterministic machine signal (e.g. heartbeat_stale, telemetry_gap,
    -- db_unreachable). NULL for summary diagnostic rows.
    signal TEXT NULL,
    severity TEXT NOT NULL
        CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    -- Lifecycle state. detected -> remediation_attempted -> verification_pending
    -- -> recovered | verification_failed | escalation_required. manual_required
    -- means a fix is recommended but no supported automatic mechanism exists.
    status TEXT NOT NULL
        CHECK (status IN (
            'detected', 'remediation_attempted', 'verification_pending',
            'recovered', 'verification_failed', 'escalation_required',
            'manual_required', 'acknowledged', 'resolved'
        )),
    summary TEXT NOT NULL DEFAULT '',
    diagnosis TEXT NULL,
    recommended_action TEXT NULL,
    -- How policy classified any action for this event.
    action_policy TEXT NULL
        CHECK (action_policy IN ('none', 'auto', 'approval_required', 'forbidden', 'unsupported')),
    action_attempted BOOLEAN NOT NULL DEFAULT FALSE,
    -- Whether the execution mechanism returned. NOT proof of recovery.
    action_result TEXT NULL
        CHECK (action_result IN ('succeeded', 'failed', 'not_supported', 'skipped', 'pending')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Stable dedupe key for an active finding (component + signal). Application
    -- code updates the existing active finding in place rather than duplicating.
    fingerprint TEXT NULL,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_healthy_at TIMESTAMPTZ NULL,
    resolved_at TIMESTAMPTZ NULL,
    verified_at TIMESTAMPTZ NULL,
    -- Actor for a human-triggered remediation (accountability also flows to
    -- audit_logs). NULL for autonomous / scheduled evaluation rows.
    created_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reliability_events_detected_desc
    ON reliability_events (detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_reliability_events_workspace_detected_desc
    ON reliability_events (workspace_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_reliability_events_component_status
    ON reliability_events (component, status);
-- Fast lookup of the active finding for a component+signal during correlation
-- (dedupe) and of recent remediation attempts during cooldown/loop-guard checks.
CREATE INDEX IF NOT EXISTS idx_reliability_events_fingerprint_type
    ON reliability_events (fingerprint, event_type, detected_at DESC);

-- 2) Persisted infra-health samples -------------------------------------------
-- Each row is one evaluation of the canonical health snapshot's critical path,
-- with MEASURED probe latency. Uptime / Avg Response / Error Rate are aggregated
-- from these rows over a real time window. With too few samples the UI shows
-- "Insufficient history" rather than a fabricated 99.97%. Samples are written
-- only by EXPLICIT actions (Run Diagnostic, remediation verification, or a
-- scheduled diagnostic) — never as a side effect of a GET.
CREATE TABLE IF NOT EXISTS reliability_health_samples (
    id UUID PRIMARY KEY,
    workspace_id UUID NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    sampled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    overall_status TEXT NOT NULL
        CHECK (overall_status IN ('healthy', 'degraded', 'failing', 'unavailable')),
    -- Uptime numerator: the critical availability path (API + database) was
    -- reachable at sample time.
    critical_ok BOOLEAN NOT NULL,
    -- Error-rate numerator: at least one critical component was failing.
    error BOOLEAN NOT NULL DEFAULT FALSE,
    -- Measured probe round-trip (DB + Redis + RPC probes). Source is the health
    -- evaluator's own timing — NEVER frontend render time.
    response_ms INTEGER NULL,
    db_latency_ms INTEGER NULL,
    redis_latency_ms INTEGER NULL,
    rpc_latency_ms INTEGER NULL,
    components JSONB NOT NULL DEFAULT '{}'::jsonb,
    source TEXT NOT NULL DEFAULT 'diagnostic'
        CHECK (source IN ('diagnostic', 'scheduled', 'remediation_verify')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reliability_health_samples_sampled_desc
    ON reliability_health_samples (sampled_at DESC);
CREATE INDEX IF NOT EXISTS idx_reliability_health_samples_workspace_sampled_desc
    ON reliability_health_samples (workspace_id, sampled_at DESC);
