-- Migration 0118: Backfill missing detected_by on wallet-transfer telemetry rows.
--
-- Root cause (data, not code): telemetry_events rows persisted by builds that
-- predate the detected_by payload stamps (native scan source_type: 2026-06-23
-- PR #1223; detected_by: 2026-06-29 PR #1254; ERC-20 log path + persist-time
-- stamping: PRs #1270-#1272) carry NO detection-path fact inside payload_json.
-- The API/UI normalization fails closed for such rows, so the customer-facing
-- "Detected By" column renders "Unknown" — e.g. the production
-- wallet_transfer_detected row at block 48150235 whose live tx the realtime
-- worker never scanned (its live tail started at 48192261, matches=0).
--
-- Classification mirrors services.api.app.worker_status.
-- classify_wallet_transfer_detected_by, and is locked against the application
-- by test_backfill_detected_by_migration_0118.py — update both together:
--   1. payload facts: detected_by / details.detected_by / metadata.detected_by,
--      then source_type (payload/details/metadata), then ingestion_source /
--      ingestion_method — each mapped through the canonical table below.
--   2. the provider_type column: realtime tags map to themselves; the
--      stable-family writer names (evm_activity_provider, monitoring_provider,
--      evm_rpc) map to stable_rpc_polling.
--   3. stable-polling inference: a LIVE wallet row with no payload markers and
--      no/blank provider_type predates the stamps, and every realtime-family
--      writer has stamped payload markers since its first commit — so the
--      writer was the stable polling family.
--
-- Canonical mapping (worker_status._canonical_detected_by_or_none):
--   realtime_websocket | realtime_backfill | realtime_tx_import
--     | quicknode_http_fast_tail | realtime_http_fast_tail  -> itself
--   tx_hash_import                                          -> realtime_tx_import
--   polling | rpc_polling | evm_rpc | rpc_backfill
--     | stable_rpc_polling                                  -> stable_rpc_polling
--
-- Truthfulness guarantees:
--   * Only evidence_source='live' wallet rows (wallet_transfer_detected /
--     native_transfer) with a real tx_hash are touched — never simulator or
--     replay rows, never retention-purged rows (their tx_hash is gone).
--   * A realtime tag is NEVER invented: rows without explicit realtime markers
--     resolve to stable_rpc_polling or stay untouched.
--   * Rows naming a foreign writer (unknown provider_type, no payload facts)
--     stay untouched and keep rendering an explicit "Unknown".
--   * Each stamped row carries detected_by_source='backfill_migration_0118' so
--     backfilled provenance is always distinguishable from writer-stamped facts.
--   * payload_hash is recomputed (sha256 of the new payload_json text) so the
--     stored hash never describes a payload other than the one persisted.
--
-- Idempotent: only rows still missing payload_json->>'detected_by' match, so
-- re-running is a no-op.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Canonical detected_by mapping, byte-for-byte the same value table as
-- worker_status._canonical_detected_by_or_none. Transient; dropped at the end.
CREATE OR REPLACE FUNCTION _decoda_canonical_detected_by_0118(p_value text) RETURNS text AS $fn$
DECLARE
    v text := lower(btrim(coalesce(p_value, '')));
BEGIN
    IF v = '' THEN
        RETURN NULL;
    END IF;
    IF v IN ('realtime_websocket', 'realtime_backfill', 'realtime_tx_import',
             'quicknode_http_fast_tail', 'realtime_http_fast_tail') THEN
        RETURN v;
    END IF;
    IF v = 'tx_hash_import' THEN
        RETURN 'realtime_tx_import';
    END IF;
    IF v IN ('polling', 'rpc_polling', 'evm_rpc', 'rpc_backfill', 'stable_rpc_polling') THEN
        RETURN 'stable_rpc_polling';
    END IF;
    RETURN NULL;
END;
$fn$ LANGUAGE plpgsql IMMUTABLE;

DO $mig$
DECLARE
    v_updated int := 0;
BEGIN
    UPDATE telemetry_events te
    SET payload_json = np.new_payload,
        payload_hash = encode(digest(np.new_payload::text, 'sha256'), 'hex')
    FROM (
        SELECT d.id,
               COALESCE(d.payload_json, '{}'::jsonb) || jsonb_build_object(
                   'detected_by', d.resolved_detected_by,
                   'detected_by_source', 'backfill_migration_0118'
               ) AS new_payload
        FROM (
            SELECT id,
                   payload_json,
                   COALESCE(
                       -- Tier 1: payload facts (detected_by copies, then
                       -- source_type copies, then ingestion markers).
                       _decoda_canonical_detected_by_0118(payload_json->>'detected_by'),
                       _decoda_canonical_detected_by_0118(payload_json->'details'->>'detected_by'),
                       _decoda_canonical_detected_by_0118(payload_json->'metadata'->>'detected_by'),
                       _decoda_canonical_detected_by_0118(payload_json->>'source_type'),
                       _decoda_canonical_detected_by_0118(payload_json->'details'->>'source_type'),
                       _decoda_canonical_detected_by_0118(payload_json->'metadata'->>'source_type'),
                       _decoda_canonical_detected_by_0118(payload_json->>'ingestion_source'),
                       _decoda_canonical_detected_by_0118(payload_json->>'ingestion_method'),
                       -- Tier 2: the provider_type column (created_by_worker).
                       _decoda_canonical_detected_by_0118(provider_type),
                       -- Tier 3: stable-polling inference for pre-stamp rows.
                       CASE
                           WHEN lower(btrim(coalesce(provider_type, ''))) IN
                                ('evm_activity_provider', 'monitoring_provider', '')
                           THEN 'stable_rpc_polling'
                       END
                   ) AS resolved_detected_by
            FROM telemetry_events
            WHERE event_type IN ('wallet_transfer_detected', 'native_transfer')
              AND evidence_source = 'live'
              AND COALESCE(payload_json->>'detected_by', '') = ''
              AND COALESCE(payload_json->>'tx_hash', payload_json->>'hash') IS NOT NULL
        ) d
        WHERE d.resolved_detected_by IS NOT NULL
    ) np
    WHERE te.id = np.id;

    GET DIAGNOSTICS v_updated = ROW_COUNT;
    RAISE NOTICE '0118 backfill complete: detected_by stamped on % wallet-transfer telemetry row(s)', v_updated;
END;
$mig$;

DROP FUNCTION IF EXISTS _decoda_canonical_detected_by_0118(text);
