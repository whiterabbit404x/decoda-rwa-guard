-- Scheduled, measurable recovery drills and deduplicated operator alerts.
ALTER TABLE recovery_validation_runs
    ADD COLUMN IF NOT EXISTS target_rto_seconds INTEGER NULL CHECK (target_rto_seconds IS NULL OR target_rto_seconds > 0),
    ADD COLUMN IF NOT EXISTS target_rpo_seconds INTEGER NULL CHECK (target_rpo_seconds IS NULL OR target_rpo_seconds >= 0),
    ADD COLUMN IF NOT EXISTS measured_rto_seconds INTEGER NULL CHECK (measured_rto_seconds IS NULL OR measured_rto_seconds >= 0),
    ADD COLUMN IF NOT EXISTS measured_rpo_seconds INTEGER NULL CHECK (measured_rpo_seconds IS NULL OR measured_rpo_seconds >= 0),
    ADD COLUMN IF NOT EXISTS integrity_checks JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS scheduled_for TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS trigger_type TEXT NOT NULL DEFAULT 'scheduler' CHECK (trigger_type IN ('scheduler','manual')),
    ADD COLUMN IF NOT EXISTS failure_code TEXT NULL,
    ADD COLUMN IF NOT EXISTS failure_message TEXT NULL,
    ADD COLUMN IF NOT EXISTS operator_alerted_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS idx_recovery_validation_runs_success
    ON recovery_validation_runs (run_type, completed_at DESC)
    WHERE status = 'passed';

CREATE TABLE IF NOT EXISTS recovery_drill_schedules (
    run_type TEXT PRIMARY KEY CHECK (run_type IN ('backup_restore','regional_failover','provider_failover')),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    cadence_seconds INTEGER NOT NULL CHECK (cadence_seconds > 0),
    max_success_age_seconds INTEGER NOT NULL CHECK (max_success_age_seconds > 0),
    target_rto_seconds INTEGER NOT NULL CHECK (target_rto_seconds > 0),
    target_rpo_seconds INTEGER NOT NULL CHECK (target_rpo_seconds >= 0),
    next_run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_started_at TIMESTAMPTZ NULL,
    last_completed_at TIMESTAMPTZ NULL,
    last_status TEXT NULL CHECK (last_status IS NULL OR last_status IN ('running','passed','failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO recovery_drill_schedules
    (run_type, cadence_seconds, max_success_age_seconds, target_rto_seconds, target_rpo_seconds)
VALUES
    ('backup_restore', 604800, 691200, 14400, 3600),
    ('regional_failover', 2592000, 3456000, 7200, 900),
    ('provider_failover', 2592000, 3456000, 3600, 300)
ON CONFLICT (run_type) DO NOTHING;

CREATE TABLE IF NOT EXISTS recovery_drill_operator_alerts (
    id UUID PRIMARY KEY,
    run_type TEXT NOT NULL CHECK (run_type IN ('backup_restore','regional_failover','provider_failover')),
    alert_kind TEXT NOT NULL CHECK (alert_kind IN ('failed','stale')),
    fingerprint TEXT NOT NULL UNIQUE,
    recovery_validation_run_id UUID NULL REFERENCES recovery_validation_runs(id) ON DELETE SET NULL,
    summary TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_alerted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_alerted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivered BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_at TIMESTAMPTZ NULL
);
CREATE INDEX IF NOT EXISTS idx_recovery_drill_operator_alerts_open
    ON recovery_drill_operator_alerts (run_type, alert_kind, first_alerted_at DESC)
    WHERE resolved_at IS NULL;
