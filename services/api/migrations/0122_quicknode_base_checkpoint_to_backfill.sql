-- Migration 0122: migrate the legacy `base` delivery checkpoint into the BACKFILL
-- lane, WITHOUT ever seeding the live lane from the old historical block.
--
-- Backs the "QuickNode still does not detect current-chain transfers" real-time fix.
-- The single QuickNode Stream replays sequentially from an old start block
-- (stream_started_at_block=48391739) and posts to /api/integrations/quicknode/streams/base,
-- advancing stream_key='base'. That checkpoint is the provider's DELIVERY high-water
-- mark, tens of thousands of blocks behind the tip — it MUST NOT be treated as live
-- health. This migration:
--
--   1. Preserves that historical progress as the BACKFILL lane's starting cursor
--      (stream_key='quicknode:base:backfill'), so the missed range is walked exactly
--      once by the lower-priority backfill lane / RPC worker.
--   2. Does NOT create or seed the LIVE lane (stream_key='quicknode:base:live'). The
--      live lane initializes ONLY from the current chain head via controlled startup
--      logic (quicknode_streams.run_live_tip_ingest / the /base-live webhook), never
--      from block 48391739 — so a 40k-block backlog can never drag the live cursor
--      backwards or make the UI claim the provider is behind when it is at the tip.
--
-- Idempotent + fail-closed:
--   * ON CONFLICT (stream_key) DO NOTHING — re-running never overwrites an already
--     advancing backfill cursor, and it can never touch the live checkpoint (a
--     different key). The runtime seeder quicknode_streams.seed_backfill_from_base_checkpoint
--     performs the same copy for deployments where this migration has not yet run.
--   * Only copies when the base lane actually has progress (last_processed_block NOT
--     NULL); a deployment that never received a base webhook simply seeds nothing.
--   * NOT added to the required-pilot-tables check: the checkpoints are auxiliary
--     operational state whose absence must never fail ingestion.

CREATE TABLE IF NOT EXISTS quicknode_stream_checkpoints (
    stream_key TEXT PRIMARY KEY,
    latest_stream_block BIGINT,
    last_processed_block BIGINT,
    missed_block_gap BIGINT NOT NULL DEFAULT 0,
    stream_started_at_block BIGINT,
    webhook_received_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO quicknode_stream_checkpoints (
    stream_key, latest_stream_block, last_processed_block, missed_block_gap,
    stream_started_at_block, webhook_received_at, updated_at
)
SELECT
    'quicknode:base:backfill',
    base.last_processed_block,
    base.last_processed_block,
    0,
    COALESCE(base.stream_started_at_block, base.last_processed_block),
    base.webhook_received_at,
    NOW()
FROM quicknode_stream_checkpoints base
WHERE base.stream_key = 'base'
  AND base.last_processed_block IS NOT NULL
ON CONFLICT (stream_key) DO NOTHING;
