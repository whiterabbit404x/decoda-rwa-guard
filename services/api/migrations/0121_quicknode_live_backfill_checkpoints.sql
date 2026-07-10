-- Migration 0121: QuickNode live + backfill checkpoint identities (real-time fix).
--
-- Backs the "new monitored-wallet transfer only appears after Stable RPC Polling"
-- fix. The single webhook checkpoint (stream_key='base', migration 0120) tracks the
-- provider stream's DELIVERY high-water mark — which, in the incident, replays from
-- an old block far behind the chain tip. The real-time path adds two INDEPENDENT
-- lanes that reuse this same table via two new logical stream_key rows:
--
--   'quicknode:base:live'      Chain-tip consumer. Begins at the current safe head
--                              (head - confirmations) and only moves forward at the
--                              tip. last_processed_block is the lag reference cursor;
--                              latest_stream_block stores the observed CHAIN HEAD so
--                              the Telemetry header can compute lag = head - cursor
--                              (Live / Catching up / Degraded / Stale) with no RPC
--                              call. NEVER written by the backfill lane.
--   'quicknode:base:backfill'  Lower-priority historical lane that walks the missed
--                              range on its OWN cursor, deduped against the live lane
--                              and Stable RPC Polling. NEVER written by the live lane.
--
-- No DDL change is required: quicknode_stream_checkpoints (0120) is keyed by
-- stream_key TEXT PRIMARY KEY, so the two lanes are simply additional rows, and the
-- API creates the table lazily (CREATE TABLE IF NOT EXISTS) as well. This migration
-- is intentionally schema-neutral and idempotent — it exists to make the new
-- checkpoint identities an explicit, reviewable part of the schema history, exactly
-- as 0120 documented the table itself. It is NOT added to the required-pilot-tables
-- check: the checkpoints are auxiliary operational state whose absence must never
-- fail ingestion (the live/backfill lanes seed themselves on first run).
--
-- Re-running is a no-op.

CREATE TABLE IF NOT EXISTS quicknode_stream_checkpoints (
    stream_key TEXT PRIMARY KEY,
    latest_stream_block BIGINT,
    last_processed_block BIGINT,
    missed_block_gap BIGINT NOT NULL DEFAULT 0,
    stream_started_at_block BIGINT,
    webhook_received_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
