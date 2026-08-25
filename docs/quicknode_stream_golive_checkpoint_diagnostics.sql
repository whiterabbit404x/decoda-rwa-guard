-- ============================================================================
-- QuickNode Stream go-live — stale live checkpoint + runtime-window diagnostics
-- ============================================================================
-- Two pre-Stream blockers, BOTH resolvable by persisted-state repair only.
-- The application code is already safe (see the analysis / tests referenced at
-- the bottom of this file). NOTHING here is a migration and NOTHING auto-runs.
-- Run the READ-ONLY blocks first, review the rows, capture the current values
-- for rollback, THEN decide whether to run a repair block.
--
-- Checkpoint storage (single source of truth):
--   Table  quicknode_stream_checkpoints
--   PK     stream_key TEXT
--   Cols   latest_stream_block BIGINT, last_processed_block BIGINT,
--          missed_block_gap BIGINT, stream_started_at_block BIGINT,
--          webhook_received_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
--   Rows   'base'                    — legacy delivery high-water lane (DO NOT TOUCH)
--          'quicknode:base:live'     — chain-tip LIVE lane   (the stale row: 48771299)
--          'quicknode:base:backfill' — historical BACKFILL lane (DO NOT TOUCH)
--
-- The startup marker
--   event=quicknode_live_lane_started checkpoint_identity=quicknode:base:live
--   chain_head=50418514 checkpoint_block=48771299 lag_blocks=1647215
-- reads last_processed_block for stream_key='quicknode:base:live'. chain_head is a
-- LIVE RPC read at boot; checkpoint_block=48771299 is the frozen persisted value a
-- previous live-lane writer left behind. lag_blocks is chain_head - checkpoint_block.
--
-- WHY NO CODE CHANGE / NO REPLAY RISK (proven in
-- services/api/tests/test_quicknode_live_stale_checkpoint_audit.py):
--   The active live path is the WEBHOOK POST /api/integrations/quicknode/streams/
--   base-live -> _process_realtime_lane_batch. It has NO gap detector and NO
--   RPC backfill; it advances quicknode:base:live via _advance_lane_checkpoint,
--   whose upsert is  last_processed_block = GREATEST(existing, incoming). So the
--   first current-tip webhook (~50418xxx) jumps the stale 48771299 straight to the
--   tip in ONE monotonic step, fetches ZERO blocks over RPC (at most one CACHED
--   eth_blockNumber for lag), and never replays the 1.6M-block gap. The skipped
--   range is owned by the independent backfill lane + 900s stable polling.
--   The repair below is therefore OPTIONAL cosmetic hygiene (a truthful pre-Stream
--   "no live activity yet" instead of a 1.6M-block lag banner), not a safety fix.
-- ============================================================================


-- ############################################################################
-- BLOCKER 1 — stale quicknode:base:live checkpoint (48771299)
-- ############################################################################

-- ----------------------------------------------------------------------------
-- (1) READ-ONLY DIAGNOSTICS — run first; they modify nothing.
--     Capture the quicknode:base:live row's values for the rollback block.
-- ----------------------------------------------------------------------------

-- 1a. All three lane checkpoints side by side. Confirms the live row is the only
--     stale one and that base / backfill are independent (do not touch them).
SELECT stream_key,
       latest_stream_block,
       last_processed_block,
       missed_block_gap,
       stream_started_at_block,
       webhook_received_at,
       updated_at
FROM quicknode_stream_checkpoints
WHERE stream_key IN ('base', 'quicknode:base:live', 'quicknode:base:backfill')
ORDER BY stream_key;

-- 1b. Just the live row — the exact bytes the repair/rollback act on.
--     COPY THIS ROW before repairing; the rollback re-inserts these values.
SELECT *
FROM quicknode_stream_checkpoints
WHERE stream_key = 'quicknode:base:live';

-- 1c. Sanity: no telemetry / alerts / incidents are keyed by this checkpoint.
--     (Answer must be independent of the checkpoint — proves the repair below
--     cannot delete or orphan any customer evidence. Expect real detection rows
--     to remain untouched: telemetry lives in telemetry_events, keyed by
--     target_id + tx_hash, never by stream_key.)
SELECT COUNT(*) AS quicknode_stream_telemetry_rows
FROM telemetry_events
WHERE payload_json->>'detected_by' IN
      ('quicknode_stream', 'quicknode_stream_backfill', 'quicknode_stream_debug_import');


-- ----------------------------------------------------------------------------
-- (2) PROPOSED ONE-TIME REPAIR — pick EXACTLY ONE option. Affects ONLY
--     stream_key='quicknode:base:live'. Never base / backfill / evidence.
-- ----------------------------------------------------------------------------

-- === OPTION A (RECOMMENDED) — reset the live checkpoint; let the FIRST
--     current-tip webhook re-establish it at the tip. ===
-- Chosen from code semantics, not an invented block number:
--   * With the row absent, _advance_lane_checkpoint INSERTs last_processed_block
--     = the first delivered tip block (~50418xxx) — the lane bootstraps AT the tip.
--   * Even the (disabled) RPC-poller worker run_live_tip_ingest cold-starts at
--     `safe_head` when prev_block IS NULL — it also begins at the tip, never
--     replays history.
--   * Pre-Stream, build_quicknode_live_lane_status() then returns state=None
--     ("no live activity yet") and the startup marker logs checkpoint_block=none
--     — the most truthful pre-Stream state (no invented lag, no false green).
-- Scoped to one PK. Deletes nothing else.
DELETE FROM quicknode_stream_checkpoints
WHERE stream_key = 'quicknode:base:live';

-- === OPTION B (ALTERNATIVE) — pin the live cursor to a current safe head.
--     Use ONLY if you want the lane pre-seeded before the Stream's first push.
--     Replace :SAFE_HEAD with a CURRENT Base head you have just read
--     (e.g. eth_blockNumber minus the 2-block confirmation offset). Do NOT
--     invent it and do NOT reuse the boot-time chain_head from an old log.
--     GREATEST-based advance means a first webhook slightly behind :SAFE_HEAD
--     is still safe (those few blocks fall to backfill + stable polling).
-- UPDATE quicknode_stream_checkpoints
--    SET last_processed_block   = :SAFE_HEAD,
--        latest_stream_block    = :SAFE_HEAD,
--        stream_started_at_block = COALESCE(stream_started_at_block, :SAFE_HEAD),
--        webhook_received_at    = NOW(),
--        updated_at             = NOW()
--  WHERE stream_key = 'quicknode:base:live';


-- ----------------------------------------------------------------------------
-- (3) ROLLBACK — restore the pre-repair live checkpoint exactly.
--     Fill the placeholders from the row you captured in step (1b).
-- ----------------------------------------------------------------------------

-- Rollback for OPTION A (re-insert the deleted row):
-- INSERT INTO quicknode_stream_checkpoints
--     (stream_key, latest_stream_block, last_processed_block, missed_block_gap,
--      stream_started_at_block, webhook_received_at, updated_at)
-- VALUES
--     ('quicknode:base:live',
--      :OLD_latest_stream_block,      -- e.g. 48771299 (or NULL if it was NULL)
--      48771299,                      -- :OLD_last_processed_block
--      :OLD_missed_block_gap,         -- e.g. 0
--      :OLD_stream_started_at_block,  -- e.g. 48771299
--      :OLD_webhook_received_at,      -- captured TIMESTAMPTZ (or NULL)
--      NOW())
-- ON CONFLICT (stream_key) DO UPDATE SET
--     latest_stream_block     = EXCLUDED.latest_stream_block,
--     last_processed_block    = EXCLUDED.last_processed_block,
--     missed_block_gap        = EXCLUDED.missed_block_gap,
--     stream_started_at_block = EXCLUDED.stream_started_at_block,
--     webhook_received_at     = EXCLUDED.webhook_received_at,
--     updated_at              = NOW();

-- Rollback for OPTION B (restore the frozen value):
-- UPDATE quicknode_stream_checkpoints
--    SET last_processed_block    = 48771299,
--        latest_stream_block     = :OLD_latest_stream_block,
--        stream_started_at_block = :OLD_stream_started_at_block,
--        webhook_received_at     = :OLD_webhook_received_at,
--        updated_at              = NOW()
--  WHERE stream_key = 'quicknode:base:live';


-- ############################################################################
-- BLOCKER 2 — runtime telemetry window still sized for a 60s target interval
-- ############################################################################
-- Symptom (API runtime-status):
--   monitoring_runtime_telemetry_window telemetry_window_seconds=300
--       max_enabled_interval_seconds=60
--   -> reporting_systems=0 fresh_live_reporting_systems=0
--      evidence_source=replay monitoring_status=limited
--
-- Root cause: max_enabled_interval_seconds is the STORED per-target interval
--   COALESCE(targets.monitoring_interval_seconds, 30). The value 60 is a real
--   stored value (the COALESCE default and MONITOR_POLL_INTERVAL_SECONDS are both
--   30, so 60 can only come from the column). The runtime "fresh reporting" window
--   floors at max(300, MONITOR_POLL_INTERVAL_SECONDS*6=180, 60 + max(120,60)=180)
--   = 300s, DECOUPLED from the worker's canonical 900s cadence (whose own
--   heartbeat/stale window is correctly 1800s). So a 900s stable poll lands
--   OUTSIDE the 300s reporting window and reads as "no fresh live reporting".
--
-- This is NOT a code bug and does NOT block go-live:
--   * Before the Stream, "limited" is the truthful, fail-closed state (acceptable).
--   * Once the Stream delivers, quicknode_stream telemetry (evidence_source='live',
--     ~1 row/2s) lands INSIDE the 300s window -> fresh_live_reporting_systems>0 ->
--     monitoring_status flips to live via the EXISTING source-agnostic semantics.
-- The repair below is OPTIONAL: it aligns the reporting window with the intended
--   900s stable-poll fallback so a Streams pause does not read as a false
--   "provider unavailable". Setting the interval to 900 grows the window to
--   max(300, 180, 900 + 120) = 1020s, so a healthy 900s poll is "fresh".

-- ----------------------------------------------------------------------------
-- (1) READ-ONLY DIAGNOSTICS — confirm which enabled targets carry interval=60
--     and capture their ids + current interval for rollback.
-- ----------------------------------------------------------------------------

-- 1a. Enabled, non-deleted Base wallet targets and their configured interval.
--     max_enabled_interval_seconds is MAX(effective_interval) over these rows.
SELECT t.id                          AS target_id,
       t.workspace_id,
       t.name,
       t.chain_network,
       t.chain_id,
       t.monitoring_enabled,
       t.enabled,
       t.monitoring_interval_seconds,                       -- the stored value (expect 60)
       COALESCE(t.monitoring_interval_seconds, 30) AS effective_interval_seconds
FROM targets t
WHERE t.deleted_at IS NULL
  AND COALESCE(t.enabled, FALSE) = TRUE
  AND COALESCE(t.monitoring_enabled, FALSE) = TRUE
  AND (LOWER(COALESCE(t.chain_network, 'base')) IN ('base', 'base-mainnet')
       OR t.chain_id = 8453)
ORDER BY t.monitoring_interval_seconds NULLS FIRST;

-- 1b. The single number the runtime log prints as max_enabled_interval_seconds.
SELECT MAX(COALESCE(t.monitoring_interval_seconds, 30)) AS max_enabled_interval_seconds
FROM targets t
WHERE t.deleted_at IS NULL
  AND COALESCE(t.enabled, FALSE) = TRUE
  AND COALESCE(t.monitoring_enabled, FALSE) = TRUE
  AND (LOWER(COALESCE(t.chain_network, 'base')) IN ('base', 'base-mainnet')
       OR t.chain_id = 8453);

-- ----------------------------------------------------------------------------
-- (2) PROPOSED REPAIR (OPTIONAL) — align the stored interval with the 900s
--     stable-poll cadence. Touches ONLY targets.monitoring_interval_seconds
--     for enabled Base targets currently below 900. No telemetry/alerts/incidents.
-- ----------------------------------------------------------------------------
-- UPDATE targets t
--    SET monitoring_interval_seconds = 900,
--        updated_at = NOW()
--  WHERE t.deleted_at IS NULL
--    AND COALESCE(t.enabled, FALSE) = TRUE
--    AND COALESCE(t.monitoring_enabled, FALSE) = TRUE
--    AND (LOWER(COALESCE(t.chain_network, 'base')) IN ('base', 'base-mainnet')
--         OR t.chain_id = 8453)
--    AND COALESCE(t.monitoring_interval_seconds, 30) < 900;

-- ----------------------------------------------------------------------------
-- (3) ROLLBACK — restore the prior interval(s). Prefer per-target restores from
--     the ids + values captured in (1a); the blanket form below assumes every
--     matched Base target was 60 before the repair.
-- ----------------------------------------------------------------------------
-- UPDATE targets t
--    SET monitoring_interval_seconds = 60,
--        updated_at = NOW()
--  WHERE t.deleted_at IS NULL
--    AND COALESCE(t.enabled, FALSE) = TRUE
--    AND COALESCE(t.monitoring_enabled, FALSE) = TRUE
--    AND (LOWER(COALESCE(t.chain_network, 'base')) IN ('base', 'base-mainnet')
--         OR t.chain_id = 8453)
--    AND t.monitoring_interval_seconds = 900;
-- ============================================================================
-- END. Do not auto-run. Proofs:
--   services/api/tests/test_quicknode_live_stale_checkpoint_audit.py       (Blocker 1)
--   services/api/tests/test_quicknode_stream_runtime_window_alignment.py   (Blocker 2)
--   services/api/tests/test_screen4_runtime_reporting_truthfulness.py      (evidence truth)
-- ============================================================================
