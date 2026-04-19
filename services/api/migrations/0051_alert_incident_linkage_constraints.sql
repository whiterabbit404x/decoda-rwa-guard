-- 0047 already creates these linkage indexes/constraints for most environments.
-- Keep this migration lightweight for startup concurrency by only backfilling
-- missing columns/constraints and avoiding full-table validation scans.
ALTER TABLE alerts
    ADD COLUMN IF NOT EXISTS detection_id UUID NULL,
    ADD COLUMN IF NOT EXISTS incident_id UUID NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'alerts_detection_id_fkey'
    ) THEN
        ALTER TABLE alerts
            ADD CONSTRAINT alerts_detection_id_fkey
            FOREIGN KEY (detection_id)
            REFERENCES detections(id)
            ON DELETE SET NULL
            NOT VALID;
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'alerts_incident_id_fkey'
    ) THEN
        ALTER TABLE alerts
            ADD CONSTRAINT alerts_incident_id_fkey
            FOREIGN KEY (incident_id)
            REFERENCES incidents(id)
            ON DELETE SET NULL
            NOT VALID;
    END IF;
END$$;

ALTER TABLE incidents
    ADD COLUMN IF NOT EXISTS source_alert_id UUID NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'incidents_source_alert_id_fkey'
    ) THEN
        ALTER TABLE incidents
            ADD CONSTRAINT incidents_source_alert_id_fkey
            FOREIGN KEY (source_alert_id)
            REFERENCES alerts(id)
            ON DELETE SET NULL
            NOT VALID;
    END IF;
END$$;
