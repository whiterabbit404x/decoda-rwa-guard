ALTER TABLE evidence
    ADD COLUMN IF NOT EXISTS monitoring_run_id UUID NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_monitoring_runs_workspace_id_id
    ON monitoring_runs (workspace_id, id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_workspace_id_id
    ON evidence (workspace_id, id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_detections_workspace_id_id
    ON detections (workspace_id, id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_detection_evidence_workspace_id_id
    ON detection_evidence (workspace_id, id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_workspace_id_id
    ON alerts (workspace_id, id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_incidents_workspace_id_id
    ON incidents (workspace_id, id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_response_actions_workspace_id_id
    ON response_actions (workspace_id, id);

UPDATE evidence e
SET monitoring_run_id = d.monitoring_run_id
FROM alerts a
JOIN detections d
  ON d.id = a.detection_id
 AND d.workspace_id = a.workspace_id
WHERE e.workspace_id = a.workspace_id
  AND e.alert_id = a.id
  AND e.monitoring_run_id IS NULL
  AND d.monitoring_run_id IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'evidence_monitoring_run_workspace_fkey'
          AND conrelid = 'evidence'::regclass
    ) THEN
        ALTER TABLE evidence
            ADD CONSTRAINT evidence_monitoring_run_workspace_fkey
            FOREIGN KEY (workspace_id, monitoring_run_id)
            REFERENCES monitoring_runs(workspace_id, id)
            ON DELETE SET NULL
            NOT VALID;
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'evidence_alert_workspace_fkey'
          AND conrelid = 'evidence'::regclass
    ) THEN
        ALTER TABLE evidence
            ADD CONSTRAINT evidence_alert_workspace_fkey
            FOREIGN KEY (workspace_id, alert_id)
            REFERENCES alerts(workspace_id, id)
            ON DELETE SET NULL
            NOT VALID;
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'detections_monitoring_run_workspace_fkey'
          AND conrelid = 'detections'::regclass
    ) THEN
        ALTER TABLE detections
            ADD CONSTRAINT detections_monitoring_run_workspace_fkey
            FOREIGN KEY (workspace_id, monitoring_run_id)
            REFERENCES monitoring_runs(workspace_id, id)
            ON DELETE SET NULL
            NOT VALID;
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'detections_alert_workspace_fkey'
          AND conrelid = 'detections'::regclass
    ) THEN
        ALTER TABLE detections
            ADD CONSTRAINT detections_alert_workspace_fkey
            FOREIGN KEY (workspace_id, linked_alert_id)
            REFERENCES alerts(workspace_id, id)
            ON DELETE SET NULL
            NOT VALID;
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'detection_evidence_detection_workspace_fkey'
          AND conrelid = 'detection_evidence'::regclass
    ) THEN
        ALTER TABLE detection_evidence
            ADD CONSTRAINT detection_evidence_detection_workspace_fkey
            FOREIGN KEY (workspace_id, detection_id)
            REFERENCES detections(workspace_id, id)
            ON DELETE CASCADE
            NOT VALID;
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'alerts_detection_workspace_fkey'
          AND conrelid = 'alerts'::regclass
    ) THEN
        ALTER TABLE alerts
            ADD CONSTRAINT alerts_detection_workspace_fkey
            FOREIGN KEY (workspace_id, detection_id)
            REFERENCES detections(workspace_id, id)
            ON DELETE SET NULL
            NOT VALID;
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'alerts_incident_workspace_fkey'
          AND conrelid = 'alerts'::regclass
    ) THEN
        ALTER TABLE alerts
            ADD CONSTRAINT alerts_incident_workspace_fkey
            FOREIGN KEY (workspace_id, incident_id)
            REFERENCES incidents(workspace_id, id)
            ON DELETE SET NULL
            NOT VALID;
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'incidents_source_alert_workspace_fkey'
          AND conrelid = 'incidents'::regclass
    ) THEN
        ALTER TABLE incidents
            ADD CONSTRAINT incidents_source_alert_workspace_fkey
            FOREIGN KEY (workspace_id, source_alert_id)
            REFERENCES alerts(workspace_id, id)
            ON DELETE SET NULL
            NOT VALID;
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'response_actions_alert_workspace_fkey'
          AND conrelid = 'response_actions'::regclass
    ) THEN
        ALTER TABLE response_actions
            ADD CONSTRAINT response_actions_alert_workspace_fkey
            FOREIGN KEY (workspace_id, alert_id)
            REFERENCES alerts(workspace_id, id)
            ON DELETE SET NULL
            NOT VALID;
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'response_actions_incident_workspace_fkey'
          AND conrelid = 'response_actions'::regclass
    ) THEN
        ALTER TABLE response_actions
            ADD CONSTRAINT response_actions_incident_workspace_fkey
            FOREIGN KEY (workspace_id, incident_id)
            REFERENCES incidents(workspace_id, id)
            ON DELETE SET NULL
            NOT VALID;
    END IF;
END$$;
