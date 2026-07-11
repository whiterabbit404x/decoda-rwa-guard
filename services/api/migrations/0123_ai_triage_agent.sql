-- AI Evidence & Triage Agent foundation.
--
-- Adds the storage for the evidence-grounded AI incident investigation layer:
-- immutable server-built evidence snapshots, the asynchronous triage job
-- lifecycle, schema-constrained AI results, citations, policy-controlled
-- recommendations with human review state, and usage/cost accounting.
--
-- All DDL is idempotent (IF NOT EXISTS / additive ALTERs) so the startup
-- migration runner can re-apply it safely. Nothing here touches the working
-- telemetry -> detection -> alert -> incident live-detection path; every table
-- is new and workspace-scoped, and the only change to an existing table is an
-- additive, NULL-tolerant dedup guard column on incidents.

-- ---------------------------------------------------------------------------
-- Immutable evidence snapshot: server-selected, versioned, deterministically
-- hashed. Raw evidence lives here, separate from any AI-generated text.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS incident_evidence_snapshots (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    schema_version TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    snapshot_json JSONB NOT NULL,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    is_complete BOOLEAN NOT NULL DEFAULT TRUE,
    incomplete_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_record_ids JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_incident_evidence_snapshots_incident
    ON incident_evidence_snapshots (workspace_id, incident_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_incident_evidence_snapshots_hash
    ON incident_evidence_snapshots (snapshot_hash);

-- ---------------------------------------------------------------------------
-- AI triage job lifecycle. One row per triage attempt. The partial unique
-- index below enforces "one active job per incident" without blocking
-- explicit regeneration once the prior job reaches a terminal state.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_triage_jobs (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    evidence_snapshot_id UUID NULL REFERENCES incident_evidence_snapshots(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN (
            'not_requested', 'queued', 'running', 'completed',
            'completed_with_warnings', 'failed', 'validation_failed',
            'disabled', 'cancelled', 'budget_blocked'
        )),
    provider TEXT NULL,
    model TEXT NULL,
    prompt_version TEXT NULL,
    evidence_schema_version TEXT NULL,
    evidence_snapshot_hash TEXT NULL,
    started_at TIMESTAMPTZ NULL,
    completed_at TIMESTAMPTZ NULL,
    latency_ms INTEGER NULL,
    input_tokens INTEGER NULL,
    output_tokens INTEGER NULL,
    estimated_cost_usd NUMERIC(12, 6) NULL,
    error_code TEXT NULL,
    error_detail TEXT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 2,
    model_response_hash TEXT NULL,
    regenerate_reason TEXT NULL,
    lease_owner TEXT NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Defensive for a pre-existing ai_triage_jobs table missing the newest columns.
ALTER TABLE ai_triage_jobs
    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS lease_owner TEXT NULL,
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS idx_ai_triage_jobs_incident
    ON ai_triage_jobs (workspace_id, incident_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_triage_jobs_status
    ON ai_triage_jobs (status, created_at);
-- Distributed-safe claim lookup for the background worker.
CREATE INDEX IF NOT EXISTS idx_ai_triage_jobs_claim
    ON ai_triage_jobs (status, next_attempt_at)
    WHERE status = 'queued';
-- One active (queued/running) triage job per incident. Regeneration is allowed
-- because a terminal-state prior job no longer participates in this index.
CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_triage_jobs_active_per_incident
    ON ai_triage_jobs (incident_id)
    WHERE status IN ('queued', 'running');

-- ---------------------------------------------------------------------------
-- Structured, schema-constrained AI result. Stores only the validated final
-- output + concise schema-defined rationale fields; never hidden reasoning.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_triage_results (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    triage_job_id UUID NOT NULL REFERENCES ai_triage_jobs(id) ON DELETE CASCADE,
    schema_version TEXT NOT NULL,
    summary TEXT NOT NULL,
    reason_triggered TEXT NULL,
    recommended_severity TEXT NULL,
    severity_confidence NUMERIC(4, 3) NULL,
    severity_reason TEXT NULL,
    result_json JSONB NOT NULL,
    warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    missing_information JSONB NOT NULL DEFAULT '[]'::jsonb,
    result_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_triage_results_incident
    ON ai_triage_results (workspace_id, incident_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_triage_results_job
    ON ai_triage_results (triage_job_id);

-- ---------------------------------------------------------------------------
-- Citations backing factual findings. Every ref must resolve to a record that
-- was present in the evidence snapshot (validated in application code).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_triage_citations (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    triage_result_id UUID NOT NULL REFERENCES ai_triage_results(id) ON DELETE CASCADE,
    ref TEXT NOT NULL,
    ref_type TEXT NOT NULL,
    description TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_triage_citations_result
    ON ai_triage_citations (triage_result_id);

-- ---------------------------------------------------------------------------
-- Policy-controlled recommendations with human review state. Recommendations
-- only ever map to predefined allowed action types / runbook IDs. Approval in
-- this phase records a decision; it never executes a high-impact action.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_recommendations (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    triage_result_id UUID NOT NULL REFERENCES ai_triage_results(id) ON DELETE CASCADE,
    action_type TEXT NOT NULL,
    runbook_id TEXT NULL,
    reason TEXT NULL,
    risk_level TEXT NOT NULL DEFAULT 'low',
    requires_human_approval BOOLEAN NOT NULL DEFAULT TRUE,
    evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    review_state TEXT NOT NULL DEFAULT 'pending_review'
        CHECK (review_state IN ('pending_review', 'accepted', 'rejected')),
    reviewed_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at TIMESTAMPTZ NULL,
    review_reason TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_recommendations_incident
    ON ai_recommendations (workspace_id, incident_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_recommendations_result
    ON ai_recommendations (triage_result_id);
CREATE INDEX IF NOT EXISTS idx_ai_recommendations_review
    ON ai_recommendations (workspace_id, review_state, created_at DESC);

-- ---------------------------------------------------------------------------
-- Usage / cost accounting for budget enforcement and reporting.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_usage_events (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    incident_id UUID NULL REFERENCES incidents(id) ON DELETE SET NULL,
    triage_job_id UUID NULL REFERENCES ai_triage_jobs(id) ON DELETE SET NULL,
    provider TEXT NULL,
    model TEXT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
    severity TEXT NULL,
    outcome TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_usage_events_workspace_day
    ON ai_usage_events (workspace_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_usage_events_global_day
    ON ai_usage_events (created_at);

-- ---------------------------------------------------------------------------
-- Additive, NULL-tolerant deterministic incident dedup guard.
--
-- Incident-level de-duplication is already provided today by upstream telemetry
-- de-dup (0113/0119), the evidence UNIQUE(target_id, tx_hash, log_index,
-- event_type) constraint, and alert->incident linkage. To make the canonical
-- identity explicit without destabilizing the live path, we add a nullable
-- dedup_key and a PARTIAL unique index that only constrains rows that populate
-- it. Existing rows (dedup_key IS NULL) are unaffected; new pipeline/AI code can
-- opt in by writing workspace_id|target_id|chain_id|tx_hash|rule_id.
-- ---------------------------------------------------------------------------
ALTER TABLE incidents
    ADD COLUMN IF NOT EXISTS dedup_key TEXT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_incidents_dedup_key
    ON incidents (workspace_id, dedup_key)
    WHERE dedup_key IS NOT NULL;
