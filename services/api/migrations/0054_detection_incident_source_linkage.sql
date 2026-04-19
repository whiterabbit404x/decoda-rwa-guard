ALTER TABLE detections
    ADD COLUMN IF NOT EXISTS evidence_source TEXT,
    ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'open',
    ADD COLUMN IF NOT EXISTS raw_evidence_json JSONB DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS monitoring_run_id UUID REFERENCES monitoring_runs(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS linked_alert_id UUID REFERENCES alerts(id) ON DELETE SET NULL;

ALTER TABLE detections
    ALTER COLUMN evidence_source SET NOT NULL,
    ALTER COLUMN status SET NOT NULL,
    ALTER COLUMN raw_evidence_json SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'detections_evidence_source_live_or_simulator'
    ) THEN
        ALTER TABLE detections
            ADD CONSTRAINT detections_evidence_source_live_or_simulator
            CHECK (evidence_source IN ('live', 'simulator'));
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'detections_monitoring_run_id_fkey'
    ) THEN
        ALTER TABLE detections
            ADD CONSTRAINT detections_monitoring_run_id_fkey
            FOREIGN KEY (monitoring_run_id)
            REFERENCES monitoring_runs(id)
            ON DELETE SET NULL;
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'detections_linked_alert_id_fkey'
    ) THEN
        ALTER TABLE detections
            ADD CONSTRAINT detections_linked_alert_id_fkey
            FOREIGN KEY (linked_alert_id)
            REFERENCES alerts(id)
            ON DELETE SET NULL;
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
            ON DELETE SET NULL;
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_detections_workspace_status_detected_at
    ON detections (workspace_id, status, detected_at DESC);

CREATE INDEX IF NOT EXISTS idx_detections_monitoring_run_id
    ON detections (monitoring_run_id);

CREATE INDEX IF NOT EXISTS idx_detections_linked_alert_id
    ON detections (linked_alert_id);

CREATE INDEX IF NOT EXISTS idx_incidents_source_alert_id
    ON incidents (source_alert_id);
