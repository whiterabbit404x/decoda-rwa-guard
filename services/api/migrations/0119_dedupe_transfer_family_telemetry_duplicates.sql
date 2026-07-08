-- Migration 0119: Collapse historical transfer-family telemetry duplicates.
--
-- Root cause (data, not code): a single on-chain transfer can be recorded twice
-- when more than one detection path sees it. QuickNode Streams always writes
-- event_type='wallet_transfer_detected' (detected_by=quicknode_stream) while the
-- stable RPC polling worker writes event_type='native_transfer' for a plain ETH
-- move (detected_by=stable_rpc_polling). The pre-0119 dedupe keyed on exact
-- event_type, so these two rows for ONE tx were never collapsed and the customer
-- saw the same transfer twice (production tx
-- 0x5bbbc797e2025a26da254e73f7393504c983bd4f8e30484fbec8fab7b662b9de on target
-- e7851a52-8fb1-48cd-84a3-d033f591c5dd, chain 8453).
--
-- Fix: for every group of transfer-family rows that share (target_id, tx_hash),
-- keep ONE canonical row and stamp the losers with
-- payload_json.duplicate_of_telemetry_id = <canonical id>. The telemetry list
-- route and the Strategic Infrastructure Guard alert backfill both exclude rows
-- carrying that key, so the duplicate stops rendering and alerts link only the
-- canonical row — WITHOUT deleting any evidence (the row and its tx_hash stay
-- queryable, only re-tagged).
--
-- Canonical winner (mirrors worker_status.CANONICAL_TRANSFER_SOURCE_PRIORITY and
-- worker_status.transfer_source_priority — update all three together):
--   quicknode_stream(0) > realtime_websocket(1) > realtime_backfill(2)
--     > realtime_tx_import(3) > quicknode_http_fast_tail(4)
--     > realtime_http_fast_tail(5) > stable_rpc_polling(6) > unknown(7)
-- Ties (same rank) are broken by EARLIEST observed_at, then id — deterministic.
-- Chain is part of the logical identity but is NOT split on: a target monitors a
-- single chain and a legacy row may not have stamped chain_id, so partitioning by
-- (target_id, tx_hash) matches the app's NULL-tolerant insert-time dedupe and can
-- never leave a duplicate uncollapsed just because one row lacks chain_id.
--
-- Truthfulness guarantees (CLAUDE.md):
--   * Only evidence_source='live' transfer-family rows with a real tx_hash are
--     considered — never simulator/replay rows, never retention-purged rows.
--   * Only the LOSING rows are touched; the surviving canonical row is never
--     stamped, so exactly one row per tx remains visible.
--   * Nothing is deleted. The duplicate is re-tagged, not removed.
--   * payload_hash is recomputed (sha256 of the new payload_json text) so the
--     stored hash never describes a payload other than the one persisted.
--
-- Idempotent: a row already carrying duplicate_of_telemetry_id is excluded from
-- the scan, so re-running collapses only newly-arrived duplicates and is a no-op
-- once a group is settled.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $mig$
DECLARE
    v_marked int := 0;
BEGIN
    UPDATE telemetry_events te
    SET payload_json = np.new_payload,
        payload_hash = encode(digest(np.new_payload::text, 'sha256'), 'hex')
    FROM (
        SELECT r.id,
               COALESCE(r.payload_json, '{}'::jsonb) || jsonb_build_object(
                   'duplicate_of_telemetry_id', r.canonical_id::text,
                   'duplicate_suppressed_reason', 'transfer_family_dedupe_0119',
                   'duplicate_of_source', r.canonical_detected_by
               ) AS new_payload
        FROM (
            SELECT
                id,
                payload_json,
                FIRST_VALUE(id) OVER w AS canonical_id,
                FIRST_VALUE(
                    lower(btrim(coalesce(payload_json->>'detected_by', '')))
                ) OVER w AS canonical_detected_by,
                COUNT(*) OVER (PARTITION BY target_id, tx_key) AS grp_size
            FROM (
                SELECT
                    id,
                    target_id,
                    payload_json,
                    observed_at,
                    lower(COALESCE(payload_json->>'tx_hash', payload_json->>'hash')) AS tx_key,
                    CASE lower(btrim(coalesce(payload_json->>'detected_by', '')))
                        WHEN 'quicknode_stream'         THEN 0
                        WHEN 'realtime_websocket'       THEN 1
                        WHEN 'realtime_backfill'        THEN 2
                        WHEN 'realtime_tx_import'       THEN 3
                        WHEN 'quicknode_http_fast_tail' THEN 4
                        WHEN 'realtime_http_fast_tail'  THEN 5
                        WHEN 'stable_rpc_polling'       THEN 6
                        ELSE
                            -- Fall back to the provider_type column for pre-stamp rows.
                            CASE
                                WHEN lower(btrim(coalesce(provider_type, ''))) = 'quicknode_stream' THEN 0
                                WHEN lower(btrim(coalesce(provider_type, ''))) IN
                                     ('evm_activity_provider', 'monitoring_provider', 'evm_rpc') THEN 6
                                ELSE 7
                            END
                    END AS canon_rank
                FROM telemetry_events
                WHERE event_type IN (
                        'wallet_transfer_detected', 'native_transfer',
                        'wallet_transfer', 'eth_transfer', 'base_native_transfer'
                    )
                  AND evidence_source = 'live'
                  AND COALESCE(payload_json->>'tx_hash', payload_json->>'hash') IS NOT NULL
                  -- Idempotent: never re-mark an already-collapsed duplicate.
                  AND payload_json->>'duplicate_of_telemetry_id' IS NULL
            ) scoped
            WINDOW w AS (
                PARTITION BY target_id, tx_key
                ORDER BY canon_rank ASC, observed_at ASC NULLS LAST, id ASC
                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
            )
        ) r
        -- Only the losers of a real duplicate group get stamped.
        WHERE r.grp_size > 1
          AND r.id <> r.canonical_id
    ) np
    WHERE te.id = np.id;

    GET DIAGNOSTICS v_marked = ROW_COUNT;
    RAISE NOTICE '0119 transfer-family dedupe: % duplicate telemetry row(s) marked duplicate_of_telemetry_id', v_marked;
END;
$mig$;
