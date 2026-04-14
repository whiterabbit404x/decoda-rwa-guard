ALTER TABLE monitored_systems
    ADD COLUMN IF NOT EXISTS last_event_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS freshness_status TEXT NOT NULL DEFAULT 'unavailable' CHECK (freshness_status IN ('fresh', 'stale', 'unavailable')),
    ADD COLUMN IF NOT EXISTS confidence_status TEXT NOT NULL DEFAULT 'unavailable' CHECK (confidence_status IN ('high', 'medium', 'low', 'unavailable')),
    ADD COLUMN IF NOT EXISTS coverage_reason TEXT NULL;

ALTER TABLE monitored_systems
    DROP CONSTRAINT IF EXISTS monitored_systems_runtime_status_check;

UPDATE monitored_systems
SET runtime_status = CASE runtime_status
    WHEN 'active' THEN 'healthy'
    WHEN 'error' THEN 'failed'
    WHEN 'offline' THEN 'disabled'
    ELSE runtime_status
END;

ALTER TABLE monitored_systems
    ADD CONSTRAINT monitored_systems_runtime_status_check
    CHECK (runtime_status IN ('provisioning', 'healthy', 'degraded', 'idle', 'failed', 'disabled'));
