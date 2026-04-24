CREATE TABLE IF NOT EXISTS detection_evidence (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    detection_id UUID NOT NULL REFERENCES detections(id) ON DELETE CASCADE,
    evidence_type TEXT NOT NULL,
    evidence_summary TEXT NOT NULL,
    source TEXT NOT NULL,
    raw_reference TEXT NULL,
    raw_payload_json JSONB NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT detection_evidence_raw_reference_or_payload_present
        CHECK (raw_reference IS NOT NULL OR raw_payload_json IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_detection_evidence_workspace_created_at
    ON detection_evidence (workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_detection_evidence_detection_id
    ON detection_evidence (detection_id);

ALTER TABLE detections
    DROP CONSTRAINT IF EXISTS detections_evidence_source_live_or_simulator;

ALTER TABLE detections
    ADD CONSTRAINT detections_evidence_source_live_or_simulator
    CHECK (evidence_source IN ('live', 'simulator', 'replay', 'none'));
