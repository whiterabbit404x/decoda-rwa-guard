-- Transactional alert outbox and durable Redis Streams worker state.

CREATE TABLE IF NOT EXISTS alert_event_outbox (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    alert_id UUID NULL REFERENCES alerts(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'leased', 'published', 'delivered', 'retry', 'dead_letter')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_owner TEXT NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    bus_event_id TEXT NULL,
    last_error TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at TIMESTAMPTZ NULL,
    delivered_at TIMESTAMPTZ NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_alert_event_outbox_claim
    ON alert_event_outbox (next_attempt_at, created_at)
    WHERE status IN ('pending', 'retry', 'leased');
CREATE INDEX IF NOT EXISTS idx_alert_event_outbox_delivery
    ON alert_event_outbox (status, published_at)
    WHERE status IN ('published', 'dead_letter');

CREATE TABLE IF NOT EXISTS alert_event_worker_state (
    worker_name TEXT PRIMARY KEY,
    worker_kind TEXT NOT NULL CHECK (worker_kind IN ('outbox_publisher', 'stream_consumer')),
    consumer_group TEXT NULL,
    consumer_name TEXT NULL,
    heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_expires_at TIMESTAMPTZ NOT NULL,
    last_success_at TIMESTAMPTZ NULL,
    last_failure_at TIMESTAMPTZ NULL,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NULL,
    last_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_alert_event_worker_state_health
    ON alert_event_worker_state (worker_kind, heartbeat_at, lease_expires_at);

-- Every committed alert insert produces an outbox row in the same transaction,
-- including alerts created by monitoring paths that do not call Python helpers.
CREATE OR REPLACE FUNCTION enqueue_alert_created_event() RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO alert_event_outbox
        (id, workspace_id, alert_id, event_type, payload, idempotency_key)
    VALUES (
        gen_random_uuid(),
        NEW.workspace_id,
        NEW.id,
        'alert.created',
        jsonb_build_object(
            'type', 'alert',
            'event_type', 'alert.created',
            'alert_id', NEW.id,
            'alert_type', NEW.alert_type,
            'title', NEW.title,
            'severity', NEW.severity,
            'status', NEW.status,
            'created_at', NEW.created_at
        ),
        'alert:' || NEW.id::text || ':created'
    )
    ON CONFLICT (idempotency_key) DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_alert_created_outbox ON alerts;
CREATE TRIGGER trg_alert_created_outbox
AFTER INSERT ON alerts
FOR EACH ROW EXECUTE FUNCTION enqueue_alert_created_event();
