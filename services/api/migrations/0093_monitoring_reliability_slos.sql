-- Durable monitoring leases and isolated end-to-end synthetic checks.
ALTER TABLE targets
    ADD COLUMN IF NOT EXISTS monitoring_lease_token UUID NULL,
    ADD COLUMN IF NOT EXISTS monitoring_lease_expires_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS monitoring_delivery_attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS monitoring_dead_lettered_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS idx_targets_monitoring_lease_expiry
    ON targets (monitoring_lease_expires_at) WHERE monitoring_enabled = TRUE;

CREATE TABLE IF NOT EXISTS monitoring_delivery_jobs (
    id UUID PRIMARY KEY,
    job_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','leased','succeeded','dead_letter')),
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts BETWEEN 1 AND 20),
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    leased_by TEXT NULL,
    leased_at TIMESTAMPTZ NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    completed_at TIMESTAMPTZ NULL,
    dead_lettered_at TIMESTAMPTZ NULL,
    last_error_code TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_monitoring_delivery_jobs_claim
    ON monitoring_delivery_jobs (status, available_at, lease_expires_at);

CREATE TABLE IF NOT EXISTS monitoring_synthetic_checks (
    id UUID PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('running','passed','failed')),
    failure_stage TEXT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ NULL
);
CREATE INDEX IF NOT EXISTS idx_monitoring_synthetic_checks_started
    ON monitoring_synthetic_checks (started_at DESC);

CREATE TABLE IF NOT EXISTS monitoring_synthetic_stages (
    check_id UUID NOT NULL REFERENCES monitoring_synthetic_checks(id) ON DELETE CASCADE,
    stage TEXT NOT NULL CHECK (stage IN ('ingestion','detection','alerting','incident_creation','evidence_persistence')),
    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (check_id, stage)
);
