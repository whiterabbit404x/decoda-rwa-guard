-- Migration 0108: Remove Base telemetry/cursors that contain Unix timestamps.
-- Base mainnet block heights are below 100,000,000 as of June 2026. Values above
-- that hard ceiling in chain_id 8453 polling data are invalid and must not remain
-- eligible for the Target Telemetry table or scanner checkpoints.

-- Delete corrupted Base RPC heartbeat rows so the UI cannot continue displaying
-- 178126xxxx after corrected polling resumes.
DELETE FROM telemetry_events
WHERE event_type IN ('rpc_polling', 'coverage_telemetry')
  AND provider_type IN ('evm_rpc', 'live_provider')
  AND COALESCE(payload_json->>'chain_id', '') = '8453'
  AND COALESCE(payload_json->>'block_number', '') ~ '^[0-9]+$'
  AND (payload_json->>'block_number')::bigint > 100000000;

-- Reset durable checkpoint rows for Base aliases.
UPDATE monitor_checkpoint
SET last_processed_block = 0,
    updated_at = NOW()
WHERE lower(chain) IN ('base', 'base-mainnet')
  AND last_processed_block > 100000000;

-- Reset target-level cursors and watcher state. The cursor prefix is the block
-- number for transaction cursors; coverage cursors are replaced on the next poll.
UPDATE targets
SET monitoring_checkpoint_cursor = NULL,
    watcher_last_observed_block = NULL,
    watcher_checkpoint_lag_blocks = NULL,
    updated_at = NOW()
WHERE lower(chain_network) IN ('base', 'base-mainnet')
  AND (
      COALESCE(watcher_last_observed_block, 0) > 100000000
      OR (
          split_part(COALESCE(monitoring_checkpoint_cursor, ''), ':', 1) ~ '^[0-9]+$'
          AND split_part(monitoring_checkpoint_cursor, ':', 1)::bigint > 100000000
      )
  );

-- Remove derived coverage records carrying the same invalid Base height.
DELETE FROM monitoring_event_receipts
WHERE receipt_kind = 'coverage_telemetry'
  AND block_number > 100000000
  AND target_id IN (
      SELECT id FROM targets WHERE lower(chain_network) IN ('base', 'base-mainnet')
  );

DELETE FROM evidence
WHERE event_type = 'coverage_telemetry'
  AND block_number > 100000000
  AND target_id IN (
      SELECT id FROM targets WHERE lower(chain_network) IN ('base', 'base-mainnet')
  );
