ALTER TABLE monitored_systems
    ADD COLUMN IF NOT EXISTS last_coverage_telemetry_at TIMESTAMPTZ NULL;

ALTER TABLE monitoring_event_receipts
    ADD COLUMN IF NOT EXISTS receipt_kind TEXT NOT NULL DEFAULT 'target_event',
    ADD COLUMN IF NOT EXISTS evidence_source TEXT NOT NULL DEFAULT 'live',
    ADD COLUMN IF NOT EXISTS telemetry_kind TEXT NULL;

UPDATE monitoring_event_receipts
SET receipt_kind = CASE
        WHEN COALESCE(event_cursor, '') LIKE 'coverage:%' THEN 'coverage_telemetry'
        ELSE receipt_kind
    END,
    telemetry_kind = CASE
        WHEN COALESCE(event_cursor, '') LIKE 'coverage:%' THEN 'coverage'
        ELSE telemetry_kind
    END
WHERE receipt_kind = 'target_event';
