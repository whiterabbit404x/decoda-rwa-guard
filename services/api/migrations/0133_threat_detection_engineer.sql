-- Threat Detection Engineer (Screen 5 — Threat Monitoring).
--
-- Adds the persistence backing the autonomous, deterministic Threat Detection
-- Engineer: clustered exploit/anomaly detections correlated from normalized
-- on-chain telemetry, the evidence links that ground every detection in real
-- stored telemetry (never fabricated), a per-workspace incremental processing
-- checkpoint so the worker never rescans full history, and a worker-liveness
-- row so the API can report worker health from a fact rather than an env flag.
--
-- All DDL is idempotent (IF NOT EXISTS / additive) so the startup migration
-- runner can re-apply it safely. Every record is workspace-scoped. Nothing here
-- touches the existing telemetry -> detections -> alert -> incident path used by
-- Screens 4/6/7; the threat_detections table is a distinct correlation layer.
--
-- Separation of concerns:
--   * A "detection" is a promoted, evidence-grounded exploit/behavioral cluster.
--   * An "anomaly" is a sub-threshold deviation (status='anomaly') that has NOT
--     yet crossed promotion criteria — surfaced honestly, never as a threat.
--   * cluster_key is the stable idempotency key: repeated telemetry from the
--     same root pattern UPDATES one row (event/evidence counts, first/last seen,
--     recomputed severity/confidence) instead of creating duplicates.

-- ---------------------------------------------------------------------------
-- threat_detections — one row per correlated detection/anomaly cluster.
--   Deterministic engine is the source of truth. score_inputs preserves the
--   severity/confidence inputs for auditability; the LLM never sets these.
--   evidence_source distinguishes live / simulator / replay so simulator data
--   is never presented as customer evidence. status='anomaly' rows are
--   sub-threshold observations, not confirmed threats.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS threat_detections (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    cluster_key TEXT NOT NULL,
    detection_type TEXT NOT NULL,
    title TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'low',
    confidence NUMERIC(4, 3) NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'anomaly'
        CHECK (status IN ('anomaly', 'open', 'investigating', 'resolved', 'dismissed')),
    chain_id BIGINT NULL,
    primary_asset_id UUID NULL REFERENCES assets(id) ON DELETE SET NULL,
    evidence_source TEXT NOT NULL DEFAULT 'live'
        CHECK (evidence_source IN ('live', 'simulator', 'replay')),
    evidence_quality TEXT NOT NULL DEFAULT 'normalized_telemetry',
    baseline_window TEXT NULL,
    event_count INTEGER NOT NULL DEFAULT 0,
    actor_count INTEGER NOT NULL DEFAULT 0,
    transaction_count INTEGER NOT NULL DEFAULT 0,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    score_inputs JSONB NOT NULL DEFAULT '{}'::jsonb,
    explanation TEXT NULL,
    recommended_next_step TEXT NULL,
    alert_eligible BOOLEAN NOT NULL DEFAULT FALSE,
    detector_version TEXT NOT NULL DEFAULT 'threat-detect-v1',
    linked_alert_id UUID NULL REFERENCES alerts(id) ON DELETE SET NULL,
    linked_incident_id UUID NULL REFERENCES incidents(id) ON DELETE SET NULL,
    ai_summary TEXT NULL,
    ai_summary_source TEXT NOT NULL DEFAULT 'deterministic',
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, cluster_key)
);

CREATE INDEX IF NOT EXISTS idx_threat_detections_workspace_status_detected
    ON threat_detections (workspace_id, status, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_threat_detections_workspace_severity
    ON threat_detections (workspace_id, severity, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_threat_detections_workspace_type
    ON threat_detections (workspace_id, detection_type, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_threat_detections_workspace_asset
    ON threat_detections (workspace_id, primary_asset_id);
CREATE INDEX IF NOT EXISTS idx_threat_detections_workspace_last_seen
    ON threat_detections (workspace_id, last_seen_at DESC);

-- ---------------------------------------------------------------------------
-- threat_detection_evidence — evidence links grounding each detection in real
--   stored telemetry. Prefers a reference to the canonical telemetry_events row
--   (telemetry_id) over duplicating raw payloads; evidence_payload holds only
--   compact derived facts. dedupe_key guarantees no duplicate evidence link is
--   attached twice when the same telemetry is re-processed.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS threat_detection_evidence (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    detection_id UUID NOT NULL REFERENCES threat_detections(id) ON DELETE CASCADE,
    telemetry_id UUID NULL REFERENCES telemetry_events(id) ON DELETE SET NULL,
    transaction_hash TEXT NULL,
    block_number BIGINT NULL,
    actor_address TEXT NULL,
    evidence_type TEXT NOT NULL,
    evidence_quality TEXT NOT NULL DEFAULT 'normalized_telemetry',
    evidence_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    dedupe_key TEXT NOT NULL,
    observed_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (detection_id, dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_threat_detection_evidence_detection
    ON threat_detection_evidence (workspace_id, detection_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_threat_detection_evidence_tx
    ON threat_detection_evidence (workspace_id, transaction_hash);

-- ---------------------------------------------------------------------------
-- threat_detection_checkpoints — per-workspace incremental processing cursor.
--   The worker only scans telemetry newer than (last_processed_at,
--   last_processed_telemetry_id), so a cycle never rescans full history and a
--   restart resumes safely from the last committed position.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS threat_detection_checkpoints (
    workspace_id UUID PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    last_processed_at TIMESTAMPTZ NULL,
    last_processed_telemetry_id UUID NULL,
    last_run_at TIMESTAMPTZ NULL,
    processed_total BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- threat_detection_worker_state — worker liveness (workspace-agnostic). Written
--   only by an ENABLED worker cycle, so a missing/stale heartbeat truthfully
--   means "not healthy". Mirrors asset_risk_worker_state.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS threat_detection_worker_state (
    worker_name TEXT PRIMARY KEY,
    heartbeat_at TIMESTAMPTZ NULL,
    last_cycle_at TIMESTAMPTZ NULL,
    last_completed_at TIMESTAMPTZ NULL,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
