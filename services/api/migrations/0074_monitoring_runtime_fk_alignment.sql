CREATE TABLE IF NOT EXISTS asset_registry (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type IN ('wallet', 'smart_contract', 'treasury_vault', 'tokenized_rwa')),
    address_or_identifier TEXT NOT NULL,
    chain TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, type, address_or_identifier, chain)
);

CREATE INDEX IF NOT EXISTS idx_asset_registry_workspace_created_desc
    ON asset_registry (workspace_id, created_at DESC);

CREATE TABLE IF NOT EXISTS monitored_targets (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    asset_id UUID NULL REFERENCES asset_registry(id) ON DELETE SET NULL,
    provider_type TEXT NOT NULL,
    target_identifier TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'inactive',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_monitored_targets_workspace_created_desc
    ON monitored_targets (workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_monitored_targets_workspace_asset
    ON monitored_targets (workspace_id, asset_id);

CREATE TABLE IF NOT EXISTS monitoring_configs (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    asset_id UUID NULL REFERENCES asset_registry(id) ON DELETE SET NULL,
    target_id UUID NOT NULL REFERENCES monitored_targets(id) ON DELETE CASCADE,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    cadence_seconds INTEGER NOT NULL DEFAULT 300 CHECK (cadence_seconds > 0),
    provider_type TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_monitoring_configs_one_enabled_per_target_workspace
    ON monitoring_configs (workspace_id, target_id)
    WHERE enabled = TRUE;

CREATE INDEX IF NOT EXISTS idx_monitoring_configs_workspace_created_desc
    ON monitoring_configs (workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_monitoring_configs_workspace_asset
    ON monitoring_configs (workspace_id, asset_id);

CREATE TABLE IF NOT EXISTS monitoring_heartbeats (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    worker_name TEXT NOT NULL,
    last_heartbeat_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'healthy',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_monitoring_heartbeats_workspace_worker
    ON monitoring_heartbeats (workspace_id, worker_name);

CREATE TABLE IF NOT EXISTS monitoring_polls (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    target_id UUID NOT NULL REFERENCES monitored_targets(id) ON DELETE CASCADE,
    poll_started_at TIMESTAMPTZ NOT NULL,
    poll_finished_at TIMESTAMPTZ NULL,
    status TEXT NOT NULL,
    error_message TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_monitoring_polls_workspace_target_started
    ON monitoring_polls (workspace_id, target_id, poll_started_at DESC);

CREATE TABLE IF NOT EXISTS telemetry_events (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    asset_id UUID NULL REFERENCES asset_registry(id) ON DELETE SET NULL,
    target_id UUID NULL REFERENCES monitored_targets(id) ON DELETE SET NULL,
    provider_type TEXT NOT NULL,
    event_type TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    evidence_source TEXT NOT NULL CHECK (evidence_source IN ('live', 'simulator', 'replay')),
    payload_hash TEXT NULL,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_telemetry_events_workspace_observed
    ON telemetry_events (workspace_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS detection_events (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    asset_id UUID NULL REFERENCES asset_registry(id) ON DELETE SET NULL,
    target_id UUID NULL REFERENCES monitored_targets(id) ON DELETE SET NULL,
    telemetry_event_id UUID NULL REFERENCES telemetry_events(id) ON DELETE SET NULL,
    detection_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    confidence NUMERIC(5,4) NULL CHECK (confidence >= 0 AND confidence <= 1),
    evidence_summary TEXT NOT NULL,
    evidence_source TEXT NOT NULL CHECK (evidence_source IN ('live', 'simulator', 'replay')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_detection_events_workspace_id_id
    ON detection_events (workspace_id, id);

CREATE INDEX IF NOT EXISTS idx_detection_events_workspace_created_desc
    ON detection_events (workspace_id, created_at DESC);

ALTER TABLE alerts
    ADD COLUMN IF NOT EXISTS detection_event_id UUID NULL,
    ADD COLUMN IF NOT EXISTS detection_event_workspace_id UUID NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'alerts_detection_event_workspace_fkey'
          AND conrelid = 'alerts'::regclass
    ) THEN
        ALTER TABLE alerts
            ADD CONSTRAINT alerts_detection_event_workspace_fkey
            FOREIGN KEY (detection_event_workspace_id, detection_event_id)
            REFERENCES detection_events(workspace_id, id)
            ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_alerts_workspace_created_desc
    ON alerts (workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_alerts_detection_event_id
    ON alerts (detection_event_id);

ALTER TABLE incidents
    ADD COLUMN IF NOT EXISTS alert_id UUID NULL,
    ADD COLUMN IF NOT EXISTS alert_workspace_id UUID NULL;

UPDATE incidents i
SET alert_id = i.source_alert_id
WHERE i.alert_id IS NULL
  AND i.source_alert_id IS NOT NULL;

UPDATE incidents i
SET alert_workspace_id = a.workspace_id
FROM alerts a
WHERE i.alert_id = a.id
  AND i.alert_workspace_id IS NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'incidents_alert_workspace_fkey'
          AND conrelid = 'incidents'::regclass
    ) THEN
        ALTER TABLE incidents
            ADD CONSTRAINT incidents_alert_workspace_fkey
            FOREIGN KEY (alert_workspace_id, alert_id)
            REFERENCES alerts(workspace_id, id)
            ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_incidents_workspace_created_desc
    ON incidents (workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_incidents_alert_id
    ON incidents (alert_id);

ALTER TABLE incident_timeline
    ADD COLUMN IF NOT EXISTS workspace_id UUID NULL,
    ADD COLUMN IF NOT EXISTS detection_event_id UUID NULL;

UPDATE incident_timeline it
SET workspace_id = i.workspace_id
FROM incidents i
WHERE it.incident_id = i.id
  AND it.workspace_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_incident_timeline_workspace_created_desc
    ON incident_timeline (workspace_id, created_at DESC);

ALTER TABLE governance_actions
    ADD COLUMN IF NOT EXISTS incident_id UUID NULL,
    ADD COLUMN IF NOT EXISTS alert_id UUID NULL,
    ADD COLUMN IF NOT EXISTS action_mode TEXT NOT NULL DEFAULT 'manual_required',
    ADD COLUMN IF NOT EXISTS recommendation TEXT NULL,
    ADD COLUMN IF NOT EXISTS executed_at TIMESTAMPTZ NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'governance_actions_incident_fkey'
          AND conrelid = 'governance_actions'::regclass
    ) THEN
        ALTER TABLE governance_actions
            ADD CONSTRAINT governance_actions_incident_fkey
            FOREIGN KEY (incident_id)
            REFERENCES incidents(id)
            ON DELETE SET NULL;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'governance_actions_alert_fkey'
          AND conrelid = 'governance_actions'::regclass
    ) THEN
        ALTER TABLE governance_actions
            ADD CONSTRAINT governance_actions_alert_fkey
            FOREIGN KEY (alert_id)
            REFERENCES alerts(id)
            ON DELETE SET NULL;
    END IF;
END $$;

ALTER TABLE governance_actions
    DROP CONSTRAINT IF EXISTS governance_actions_action_mode_check;

UPDATE governance_actions
SET action_mode = CASE
    WHEN action_mode IN ('recommendation', 'simulation', 'manual_required', 'executed') THEN action_mode
    WHEN action_mode IS NULL OR btrim(action_mode) = '' THEN 'manual_required'
    WHEN lower(action_mode) IN ('manual', 'manual_only', 'pending_manual', 'requires_manual') THEN 'manual_required'
    WHEN lower(action_mode) IN ('recommended', 'recommend') THEN 'recommendation'
    WHEN lower(action_mode) IN ('simulate', 'simulated') THEN 'simulation'
    WHEN lower(action_mode) IN ('execute', 'executing', 'completed') THEN 'executed'
    ELSE 'manual_required'
END;

ALTER TABLE governance_actions
    ADD CONSTRAINT governance_actions_action_mode_check
    CHECK (action_mode IN ('recommendation', 'simulation', 'manual_required', 'executed'));

ALTER TABLE governance_actions
    DROP CONSTRAINT IF EXISTS governance_actions_action_type_check;

UPDATE governance_actions
SET action_type = CASE
    WHEN action_type IN ('freeze_wallet', 'block_transaction', 'revoke_permission', 'escalate_incident', 'apply_compliance_rule') THEN action_type
    WHEN action_type IS NULL OR btrim(action_type) = '' THEN 'escalate_incident'
    WHEN lower(action_type) IN ('revoke_approval', 'revoke_allowance', 'revoke_access', 'revoke_role') THEN 'revoke_permission'
    WHEN lower(action_type) IN ('notify_team', 'escalate', 'open_incident') THEN 'escalate_incident'
    WHEN lower(action_type) IN ('freeze', 'freeze_account', 'freeze_address') THEN 'freeze_wallet'
    WHEN lower(action_type) IN ('block_tx', 'block_transfer') THEN 'block_transaction'
    WHEN lower(action_type) IN ('apply_rule', 'compliance_rule') THEN 'apply_compliance_rule'
    ELSE 'escalate_incident'
END;

ALTER TABLE governance_actions
    ADD CONSTRAINT governance_actions_action_type_check
    CHECK (
        action_type IN (
            'freeze_wallet',
            'block_transaction',
            'revoke_permission',
            'escalate_incident',
            'apply_compliance_rule'
        )
    );

CREATE INDEX IF NOT EXISTS idx_governance_actions_workspace_created_desc
    ON governance_actions (workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_governance_actions_incident_id
    ON governance_actions (incident_id);

CREATE INDEX IF NOT EXISTS idx_governance_actions_alert_id
    ON governance_actions (alert_id);
