-- Migration 0120: QuickNode Stream checkpoint tracking (gap detection + backfill).
--
-- Backs the "QuickNode Stream missed a fresh tx" fix. The live webhook advances a
-- per-stream high-water mark on every batch so a jump from block A to block B where
-- B > A + 1 is provable (quicknode_stream_gap_detected) and self-healing: the
-- skipped blocks A+1..B-1 are re-fetched from Base RPC and run through the same
-- matcher, persisting detected_by=quicknode_stream_backfill. Stable RPC polling
-- stays the always-on backup; this only closes the window between a missed stream
-- block and the next poll.
--
-- One row per logical stream (stream_key='base' today). The API also creates this
-- table lazily (CREATE TABLE IF NOT EXISTS in
-- services/api/app/quicknode_streams.py) so the webhook works before this migration
-- runs; this migration makes the table an explicit, reviewable part of the schema.
-- It is intentionally NOT added to the required-pilot-tables check: the checkpoint
-- is auxiliary operational state and its absence must never fail the webhook.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS, so re-running is a no-op.

CREATE TABLE IF NOT EXISTS quicknode_stream_checkpoints (
    stream_key TEXT PRIMARY KEY,
    -- Highest block the stream has ever delivered (proves how far it has advanced).
    latest_stream_block BIGINT,
    -- High-water mark of blocks whose delivery has been accounted for. The gap
    -- detector compares the next batch's first block against this value.
    last_processed_block BIGINT,
    -- Size of the most recently detected gap (B - A - 1); 0 when the last batch was
    -- contiguous. A non-zero value is a diagnostic breadcrumb, not a running total.
    missed_block_gap BIGINT NOT NULL DEFAULT 0,
    -- The first block the stream ever reported. A tx below this was never covered by
    -- the stream (classified stream_already_past_block) — exactly the incident shape.
    stream_started_at_block BIGINT,
    -- Wall-clock time the most recent webhook that advanced this checkpoint arrived.
    webhook_received_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
