-- Autonomous Onboarding Agent (Screen 1) foundation.
--
-- Adds durable, workspace-scoped storage for the AI Onboarding Agent that
-- discovers blockchain infrastructure, benchmarks RPC providers, generates a
-- reviewable monitoring proposal, and (after explicit approval) idempotently
-- activates the real assets / targets / monitoring the existing UI and workers
-- already consume. Postgres is the source of truth for all session, step,
-- finding, benchmark, proposal, approval and job state; Redis is used only for
-- live SSE fan-out. Every table is new and workspace-scoped.
--
-- All DDL is idempotent (IF NOT EXISTS / additive) so the startup migration
-- runner can re-apply it safely. Nothing here modifies an existing table.

-- ---------------------------------------------------------------------------
-- Session: one onboarding run per (workspace, primary contract) attempt.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS onboarding_sessions (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN (
            'draft', 'discovering', 'partial', 'benchmarking',
            'proposal_ready', 'approved', 'activating', 'completed',
            'failed'
        )),
    current_step TEXT NULL,
    selected_chain_id INTEGER NULL,
    chain_network TEXT NULL,
    primary_contract TEXT NULL,
    protocol_name TEXT NULL,
    monitoring_mode TEXT NOT NULL DEFAULT 'recommended'
        CHECK (monitoring_mode IN ('recommended', 'strict', 'custom')),
    workspace_name TEXT NULL,
    proposal_version INTEGER NOT NULL DEFAULT 0,
    activation_status TEXT NOT NULL DEFAULT 'not_started'
        CHECK (activation_status IN ('not_started', 'pending', 'in_progress', 'completed', 'failed')),
    error_code TEXT NULL,
    error_message TEXT NULL,
    correlation_id TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_onboarding_sessions_workspace_created
    ON onboarding_sessions (workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_onboarding_sessions_workspace_status
    ON onboarding_sessions (workspace_id, status, updated_at DESC);
-- At most one non-terminal (resumable) session per workspace so "resume" is
-- unambiguous. Terminal (completed / failed) sessions are unconstrained.
CREATE UNIQUE INDEX IF NOT EXISTS idx_onboarding_sessions_one_active_per_workspace
    ON onboarding_sessions (workspace_id)
    WHERE status NOT IN ('completed', 'failed');

-- ---------------------------------------------------------------------------
-- Inputs: primary/additional contracts, custom RPC endpoints, known oracle /
-- admin addresses. RPC URLs may embed provider API keys, so the full URL is
-- stored ENCRYPTED (secret_crypto) and never in plaintext; only a redacted,
-- key-free display form + host are stored in the clear.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS onboarding_inputs (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES onboarding_sessions(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    input_type TEXT NOT NULL
        CHECK (input_type IN (
            'primary_contract', 'additional_contract', 'rpc_endpoint',
            'oracle_address', 'admin_address', 'expected_standard'
        )),
    value TEXT NULL,                 -- redacted / key-free display value (host, address, redacted URL)
    encrypted_value TEXT NULL,       -- secret_crypto ciphertext for rpc_endpoint full URL; NULL otherwise
    endpoint_host TEXT NULL,         -- host only, for rpc_endpoint rows
    label TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (session_id, input_type, value)
);

CREATE INDEX IF NOT EXISTS idx_onboarding_inputs_session
    ON onboarding_inputs (session_id, input_type);

-- ---------------------------------------------------------------------------
-- Steps: the AI Onboarding Agent execution timeline. Persisted BEFORE the SSE
-- event is published so a refresh always restores the true state.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS onboarding_steps (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES onboarding_sessions(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    step_key TEXT NOT NULL,
    sequence INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'needs_attention', 'failed', 'skipped')),
    title TEXT NOT NULL,
    result_summary TEXT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code TEXT NULL,
    error_message TEXT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ NULL,
    completed_at TIMESTAMPTZ NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (session_id, step_key)
);

CREATE INDEX IF NOT EXISTS idx_onboarding_steps_session_sequence
    ON onboarding_steps (session_id, sequence);

-- ---------------------------------------------------------------------------
-- Discovery findings. Unique on (session, finding_type, value) so retries and
-- re-discovery never create duplicate findings.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS discovery_findings (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES onboarding_sessions(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    finding_type TEXT NOT NULL,
    value TEXT NULL,
    detection_method TEXT NOT NULL,
    source_contract TEXT NULL,
    block_number BIGINT NULL,
    rpc_source_host TEXT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_hash TEXT NULL,
    confidence TEXT NOT NULL DEFAULT 'unknown'
        CHECK (confidence IN ('confirmed', 'probable', 'unknown', 'requires_review')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (session_id, finding_type, value)
);

CREATE INDEX IF NOT EXISTS idx_discovery_findings_session
    ON discovery_findings (session_id, finding_type);

-- ---------------------------------------------------------------------------
-- RPC benchmark runs + per-endpoint results.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rpc_benchmark_runs (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES onboarding_sessions(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'completed', 'failed')),
    selected_chain_id INTEGER NULL,
    best_block BIGINT NULL,
    primary_host TEXT NULL,
    fallback_host TEXT NULL,
    explanation TEXT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rpc_benchmark_runs_session
    ON rpc_benchmark_runs (session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS rpc_benchmark_results (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES rpc_benchmark_runs(id) ON DELETE CASCADE,
    session_id UUID NOT NULL REFERENCES onboarding_sessions(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    endpoint_host TEXT NOT NULL,          -- host only; never the full key-bearing URL
    redacted_url TEXT NULL,               -- display URL with any API key path/query redacted
    connection_status TEXT NOT NULL,      -- ok | dns_error | timeout | error | rate_limited
    median_latency_ms INTEGER NULL,
    p95_latency_ms INTEGER NULL,
    success_rate NUMERIC(5,4) NULL,
    error_rate NUMERIC(5,4) NULL,
    timeout_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    latest_block BIGINT NULL,
    block_lag BIGINT NULL,
    chain_id_returned INTEGER NULL,
    chain_id_ok BOOLEAN NULL,
    rate_limited BOOLEAN NOT NULL DEFAULT FALSE,
    archive_supported BOOLEAN NULL,
    score NUMERIC(10,4) NULL,
    recommendation TEXT NOT NULL DEFAULT 'degraded'
        CHECK (recommendation IN ('primary', 'fallback', 'degraded', 'rejected')),
    reason TEXT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rpc_benchmark_results_run
    ON rpc_benchmark_results (run_id);
CREATE INDEX IF NOT EXISTS idx_rpc_benchmark_results_session
    ON rpc_benchmark_results (session_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Generated workspace proposals (versioned). The proposal is a reviewable
-- draft; it never activates anything on its own.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS generated_workspace_proposals (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES onboarding_sessions(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    proposal JSONB NOT NULL DEFAULT '{}'::jsonb,
    summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    ai_summary TEXT NULL,
    ai_available BOOLEAN NOT NULL DEFAULT FALSE,
    proposal_hash TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (session_id, version)
);

CREATE INDEX IF NOT EXISTS idx_generated_workspace_proposals_session
    ON generated_workspace_proposals (session_id, version DESC);

-- ---------------------------------------------------------------------------
-- Approvals: an explicit human decision on a specific proposal version.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS onboarding_approvals (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES onboarding_sessions(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    proposal_version INTEGER NOT NULL,
    decision TEXT NOT NULL DEFAULT 'approved' CHECK (decision IN ('approved', 'rejected')),
    notes TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (session_id, proposal_version, decision)
);

CREATE INDEX IF NOT EXISTS idx_onboarding_approvals_session
    ON onboarding_approvals (session_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Agent runs: durable background job / lease records for discover, benchmark
-- and activate. Claimed via a distributed-safe conditional UPDATE. The unique
-- idempotency_key makes activation replay-safe (a repeated activate returns the
-- existing run instead of creating duplicate assets / targets).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS onboarding_agent_runs (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES onboarding_sessions(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    run_type TEXT NOT NULL CHECK (run_type IN ('discover', 'rpc_benchmark', 'activate')),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    idempotency_key TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_owner TEXT NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    worker_id TEXT NULL,
    commit_sha TEXT NULL,
    result JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code TEXT NULL,
    error_message TEXT NULL,
    started_at TIMESTAMPTZ NULL,
    finished_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_onboarding_agent_runs_claim
    ON onboarding_agent_runs (status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_onboarding_agent_runs_session
    ON onboarding_agent_runs (session_id, created_at DESC);
