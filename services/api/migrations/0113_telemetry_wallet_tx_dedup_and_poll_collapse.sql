-- Fix telemetry table flooding by RPC polling heartbeat rows.
--
-- Problem: Every poll cycle writes a new rpc_polling row with block_number in the
-- idempotency key, so thousands of rows accumulate and bury wallet_transfer_detected
-- evidence.
--
-- Fix 1: Collapse existing rpc_polling flood — keep only the latest row per target,
--         then remap its idempotency key to the collapsed format so future polls
--         update it in place rather than inserting new rows.
--
-- Fix 2: Add a partial unique index to prevent duplicate wallet_transfer_detected
--         rows for the same tx_hash per target (idempotent by content).

-- Step 1: Delete all rpc_polling duplicates, keeping only the latest per (workspace, target).
WITH latest_polls AS (
    SELECT DISTINCT ON (workspace_id, target_id) id
    FROM telemetry_events
    WHERE event_type = 'rpc_polling'
    ORDER BY workspace_id, target_id, observed_at DESC NULLS LAST
)
DELETE FROM telemetry_events
WHERE event_type = 'rpc_polling'
  AND id NOT IN (SELECT id FROM latest_polls);

-- Step 2: Remap the surviving rpc_polling row's idempotency key to the
--         collapsed format: {workspace_id}:{target_id}:coverage_poll
--         (no block_number suffix).  Future polls will ON CONFLICT DO UPDATE
--         against this key, keeping exactly 1 heartbeat row per target.
UPDATE telemetry_events
SET idempotency_key = workspace_id::text || ':' || target_id::text || ':coverage_poll'
WHERE event_type = 'rpc_polling';

-- Step 3: Unique index to deduplicate wallet_transfer_detected rows by tx_hash.
--         Prevents repeated polling from inserting the same transfer twice.
--         Scope: per target, case-insensitive tx_hash.
CREATE UNIQUE INDEX IF NOT EXISTS idx_telemetry_events_wallet_tx_dedup
    ON telemetry_events (target_id, lower(payload_json->>'tx_hash'), event_type)
    WHERE event_type = 'wallet_transfer_detected'
      AND payload_json->>'tx_hash' IS NOT NULL;

-- Composite index to accelerate the telemetry page default query
-- (prioritised wallet_transfer_detected rows, descending observed_at).
CREATE INDEX IF NOT EXISTS idx_telemetry_events_target_type_observed
    ON telemetry_events (target_id, event_type, observed_at DESC);
