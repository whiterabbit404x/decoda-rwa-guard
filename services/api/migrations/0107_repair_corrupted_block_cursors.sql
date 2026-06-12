-- Migration 0107: Repair corrupted block cursors and telemetry block numbers.
--
-- Root cause: monitoring_runner.py used `provider_result.latest_block or int(observed_at.timestamp())`
-- as a fallback when the RPC probe did not return a block number.  This stored Unix timestamps
-- (~1_781_265_978 for June 2026) as block_number in telemetry_events.payload_json.
-- Migration 0104 Part B compounded the issue by mass-backfilling null block_number fields
-- with the same timestamp pattern.
--
-- Once a timestamp was stored in monitor_checkpoint.last_processed_block, the scanner
-- computed from_block = 1_781_265_953 >> latest_block (~47_238_026 for Base), causing
-- the `safe_to < from_block` early-exit to fire every cycle — silencing all detection.
--
-- A block number > 500_000_000 is not a valid height for any monitored chain as of June 2026
-- (Base ~47M, Ethereum ~20M).  Values in that range are corrupted timestamps.
--
-- Part A: Reset corrupted scanner cursors in monitor_checkpoint.
UPDATE monitor_checkpoint
SET last_processed_block = 0,
    updated_at = NOW()
WHERE last_processed_block > 500000000;

-- Part B: Remove corrupted block_number from telemetry_events payload_json.
-- Rows where block_number is a timestamp are invisible to correct searching by block
-- and mislead the UI.  Removing the key makes them invisible to
-- canonical_last_telemetry_at (which filters COALESCE(block_number,'') <> ''),
-- ensuring only rows with real block heights are surfaced.  New correct rows will
-- be produced by the next monitoring cycle once the code fix is deployed.
UPDATE telemetry_events
SET payload_json = payload_json - 'block_number'
WHERE event_type IN ('rpc_polling', 'coverage_telemetry')
  AND provider_type IN ('evm_rpc', 'live_provider')
  AND COALESCE((payload_json->>'block_number')::text, '') ~ '^[0-9]+$'
  AND (payload_json->>'block_number')::bigint > 500000000;
