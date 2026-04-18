CREATE TABLE IF NOT EXISTS detections (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    monitored_system_id UUID NULL REFERENCES monitored_systems(id) ON DELETE SET NULL,
    protected_asset_id UUID NULL REFERENCES assets(id) ON DELETE SET NULL,
    detection_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    confidence DOUBLE PRECISION NULL,
    title TEXT NOT NULL,
    evidence_summary TEXT NOT NULL,
    evidence_source TEXT NOT NULL,
    source_rule TEXT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    monitoring_run_id UUID NULL REFERENCES monitoring_runs(id) ON DELETE SET NULL,
    linked_alert_id UUID NULL REFERENCES alerts(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE detections
    ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS monitored_system_id UUID REFERENCES monitored_systems(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS protected_asset_id UUID REFERENCES assets(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS detection_type TEXT,
    ADD COLUMN IF NOT EXISTS severity TEXT,
    ADD COLUMN IF NOT EXISTS confidence DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS title TEXT,
    ADD COLUMN IF NOT EXISTS evidence_summary TEXT,
    ADD COLUMN IF NOT EXISTS evidence_source TEXT,
    ADD COLUMN IF NOT EXISTS source_rule TEXT,
    ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'open',
    ADD COLUMN IF NOT EXISTS detected_at TIMESTAMPTZ DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS raw_evidence_json JSONB DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS monitoring_run_id UUID REFERENCES monitoring_runs(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS linked_alert_id UUID REFERENCES alerts(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

ALTER TABLE detections
    ALTER COLUMN workspace_id SET NOT NULL,
    ALTER COLUMN detection_type SET NOT NULL,
    ALTER COLUMN severity SET NOT NULL,
    ALTER COLUMN title SET NOT NULL,
    ALTER COLUMN evidence_summary SET NOT NULL,
    ALTER COLUMN evidence_source SET NOT NULL,
    ALTER COLUMN status SET NOT NULL,
    ALTER COLUMN detected_at SET NOT NULL,
    ALTER COLUMN raw_evidence_json SET NOT NULL,
    ALTER COLUMN created_at SET NOT NULL,
    ALTER COLUMN updated_at SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_detections_workspace_detected_at
    ON detections (workspace_id, detected_at DESC);

CREATE INDEX IF NOT EXISTS idx_detections_workspace_status
    ON detections (workspace_id, status, detected_at DESC);
