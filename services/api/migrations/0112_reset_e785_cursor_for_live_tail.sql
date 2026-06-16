-- Migration 0112: Reset cursor and clear dead-letter state for Base target e7851a52.
--
-- After migrations 0109-0111 the worker still reports skipped_dead_lettered=1 for
-- this target because the old block cursor (47286496) puts it ~90k blocks behind the
-- chain tip.  With MAX_BLOCKS_PER_CYCLE=1000 it would take 90+ worker cycles to catch
-- up, and each cycle can re-trigger the same stale tx (0x42eb6f...a517) through the
-- replay window, potentially causing FK or constraint errors that re-dead-letter it.
--
-- Fix:
--   1. Clear dead_lettered state and all lease/claim fields (idempotent).
--   2. Null the monitoring_checkpoint_cursor so the EVM provider starts fresh with
--      its configured backfill window (EVM_LIVE_TAIL_BLOCKS=300 default for Base).
--   3. Null watcher_last_observed_block so _load_checkpoint uses fallback_block=0,
--      which forces the monitor_checkpoint row to be used or zeroed on next cycle.
--   4. Delete the stale monitor_checkpoint row for this workspace+chain so the
--      next poll scans only the configured live-tail / backfill window rather than
--      replaying 90k blocks from the old cursor position.
--
-- Workspace-scoped and idempotent.

-- Step 1: Clear dead-letter / lease / cursor state on the target row.
UPDATE targets
SET monitoring_dead_lettered_at       = NULL,
    monitoring_delivery_attempts      = 0,
    monitoring_claimed_by             = NULL,
    monitoring_claimed_at             = NULL,
    monitoring_lease_token            = NULL,
    monitoring_lease_expires_at       = NULL,
    monitoring_checkpoint_cursor      = NULL,
    watcher_last_observed_block       = NULL,
    watcher_checkpoint_lag_blocks     = NULL,
    last_run_status                   = 'recovered',
    updated_at                        = NOW()
WHERE id           = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'::uuid
  AND workspace_id = '1155f479-3e5b-4d90-be6c-fd6c1d6b957d'::uuid
  AND deleted_at IS NULL;

-- Step 2: Delete the stale monitor_checkpoint row so _load_checkpoint returns 0
-- (fallback_block) and the EVM provider calculates from_block from the live-tail
-- window (latest_block - EVM_LIVE_TAIL_BLOCKS, default 300 for Base).
DELETE FROM monitor_checkpoint
WHERE workspace_id = '1155f479-3e5b-4d90-be6c-fd6c1d6b957d'::uuid
  AND LOWER(chain) IN ('base', 'base-mainnet');
