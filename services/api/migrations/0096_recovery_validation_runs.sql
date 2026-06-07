-- Durable evidence that isolated restore drills and regional recovery exercises ran.
CREATE TABLE IF NOT EXISTS recovery_validation_runs (
    id UUID PRIMARY KEY,
    run_type TEXT NOT NULL CHECK (run_type IN ('backup_restore','regional_failover','provider_failover')),
    environment TEXT NOT NULL,
    source_region TEXT NULL,
    recovery_region TEXT NULL,
    backup_identifier TEXT NULL,
    status TEXT NOT NULL CHECK (status IN ('running','passed','failed')),
    audit_chain_valid BOOLEAN NULL,
    evidence_chain_valid BOOLEAN NULL,
    database_checks JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_recovery_validation_runs_type_started
    ON recovery_validation_runs (run_type, started_at DESC);
