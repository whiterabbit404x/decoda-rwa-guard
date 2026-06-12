-- Index supporting tx_hash / from / to searches on telemetry_events payload_json.
-- GIN index covers all JSONB operators used by list_target_telemetry ?q= backend search.
CREATE INDEX IF NOT EXISTS idx_telemetry_events_payload_gin
    ON telemetry_events USING GIN (payload_json jsonb_path_ops);

-- Functional index on tx_hash for direct equality lookups (fastest path).
CREATE INDEX IF NOT EXISTS idx_telemetry_events_tx_hash
    ON telemetry_events ((lower(payload_json->>'tx_hash')))
    WHERE payload_json->>'tx_hash' IS NOT NULL;

-- Partial index on event_type for fast wallet_transfer_detected queries.
CREATE INDEX IF NOT EXISTS idx_telemetry_events_wallet_transfers
    ON telemetry_events (workspace_id, target_id, observed_at DESC)
    WHERE event_type = 'wallet_transfer_detected';
