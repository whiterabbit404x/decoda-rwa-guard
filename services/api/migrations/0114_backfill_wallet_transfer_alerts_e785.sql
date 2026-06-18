-- Migration 0114: Backfill missing wallet-transfer alerts for Base target e785…
--
-- Root cause (data, not code): target e7851a52-8fb1-48cd-84a3-d033f591c5dd
-- (workspace 1155f479-3e5b-4d90-be6c-fd6c1d6b957d, Base/chain 8453, wallet
-- 0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f) has live wallet_transfer_detected
-- telemetry whose alerts were never created — e.g. a transaction observed before
-- the smoke / Strategic Infrastructure Guard rules were deployed, or whose live
-- alert creation rolled back. The alert-creation code (monitoring_runner.
-- backfill_missing_alerts_for_target / _wallet_transfer_smoke_alert /
-- _strategic_infrastructure_guard_alert) is correct and applies NO recency cutoff,
-- but nothing had re-run it for this target's historical rows, so the older
-- transaction stayed invisible in the telemetry "Alerts only" filter.
--
-- This migration creates the missing alerts directly, byte-for-byte compatible
-- with the application:
--   * detection ids use the SAME UUID5 seed the app uses
--     (uuid5(NAMESPACE_DNS, 'detection:' || json{...sorted}))
--   * alert dedupe_signature uses the SAME UUID5 key
--     (workspace_id + target_id + chain_id + tx_hash + rule_key)
-- so the app's idempotency (ON CONFLICT on the deterministic detection id +
-- linked_alert_id check) recognises these rows and will NEVER create a duplicate
-- if /ops/monitoring/targets/{id}/backfill-alerts is run afterwards.
--
-- Truthfulness: only evidence_source='live' rows with a real tx_hash on chain 8453
-- are backfilled — never simulator/replay data. The direction-agnostic smoke rule
-- creates one Critical alert per unique tx_hash; the outbound-only Strategic
-- Infrastructure Guard rule additionally fires when from == the monitored wallet.
--
-- Workspace + target scoped and idempotent: safe to re-run, touches only this
-- single tenant target, and is a no-op once the alerts exist. Freshness / banner
-- / proof-chain logic is untouched.
--
-- The UUID5 seeds / dedupe-signature constants below are locked against the
-- application by test_deterministic_ids_match_migration_0114_constants in
-- services/api/tests/test_backfill_missing_alerts_for_target.py — update both
-- together if the dedupe contract ever changes.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Deterministic UUID5 in the DNS namespace, byte-identical to Python's
-- uuid.uuid5(uuid.NAMESPACE_DNS, name). Created transiently and dropped at the
-- end of the migration so it leaves no schema artifact.
CREATE OR REPLACE FUNCTION _decoda_uuid5_dns_0114(p_name text) RETURNS uuid AS $fn$
DECLARE
    h bytea;
BEGIN
    -- SHA1(namespace_bytes || utf8(name)); NAMESPACE_DNS = 6ba7b810-9dad-11d1-80b4-00c04fd430c8
    h := substring(
        digest('\x6ba7b8109dad11d180b400c04fd430c8'::bytea || convert_to(p_name, 'UTF8'), 'sha1')
        from 1 for 16
    );
    h := set_byte(h, 6, (get_byte(h, 6) & 15) | 80);    -- version 5 (0x50)
    h := set_byte(h, 8, (get_byte(h, 8) & 63) | 128);   -- RFC 4122 variant (0x80)
    RETURN encode(h, 'hex')::uuid;
END;
$fn$ LANGUAGE plpgsql IMMUTABLE;

DO $mig$
DECLARE
    v_wid       uuid := '1155f479-3e5b-4d90-be6c-fd6c1d6b957d';
    v_tid       uuid := 'e7851a52-8fb1-48cd-84a3-d033f591c5dd';
    v_user_id   uuid;
    v_name      text;
    v_wallet    text;
    rec         RECORD;
    v_tx        text;
    v_from      text;
    v_to        text;
    v_value     text;
    v_block     text;
    v_chain     text;
    v_run       uuid;
    v_smoke_det uuid;
    v_smoke_sig text;
    v_smoke_alert uuid;
    v_sig_det   uuid;
    v_sig_sig   text;
    v_sig_alert uuid;
    v_created   int := 0;
BEGIN
    -- alerts.user_id is NOT NULL, so own the backfilled alerts with the target's
    -- own user. Skip cleanly when the target is absent (fresh / other database).
    SELECT name, wallet_address, COALESCE(updated_by_user_id, created_by_user_id)
      INTO v_name, v_wallet, v_user_id
      FROM targets
     WHERE id = v_tid AND workspace_id = v_wid AND deleted_at IS NULL;

    IF v_user_id IS NULL THEN
        RAISE NOTICE '0114 skip: target % not present in workspace %', v_tid, v_wid;
        RETURN;
    END IF;

    FOR rec IN
        SELECT te.id AS telemetry_id, te.payload_json AS p
        FROM telemetry_events te
        WHERE te.workspace_id = v_wid
          AND te.target_id = v_tid
          AND te.event_type = 'wallet_transfer_detected'
          AND te.evidence_source = 'live'
          AND COALESCE(te.payload_json->>'tx_hash', te.payload_json->>'hash') IS NOT NULL
          AND COALESCE(te.payload_json->>'chain_id', '8453') = '8453'
        ORDER BY te.observed_at DESC
    LOOP
        v_tx    := COALESCE(rec.p->>'tx_hash', rec.p->>'hash');
        v_from  := lower(COALESCE(rec.p->>'from', rec.p->>'owner', ''));
        v_to    := COALESCE(rec.p->>'to', '');
        v_value := COALESCE(rec.p->>'value', rec.p->>'amount_wei', rec.p->>'amount', '0');
        v_block := rec.p->>'block_number';
        v_chain := COALESCE(rec.p->>'chain_id', '8453');

        -- ===== Smoke rule: one Critical alert for every live wallet transfer =====
        -- Detection seed (no chain_id), smoke signature (chain_id as STRING).
        v_smoke_det := _decoda_uuid5_dns_0114(
            'detection:{"rule": "smoke_wallet_transfer", "target_id": "'
            || v_tid::text || '", "tx_hash": "' || v_tx || '"}');
        v_smoke_sig := replace(_decoda_uuid5_dns_0114(
            '{"chain_id": "' || v_chain || '", "rule": "smoke_wallet_transfer", "target_id": "'
            || v_tid::text || '", "tx_hash": "' || v_tx || '", "workspace_id": "'
            || v_wid::text || '"}')::text, '-', '');

        IF NOT EXISTS (
            SELECT 1 FROM alerts
             WHERE workspace_id = v_wid AND target_id = v_tid AND dedupe_signature = v_smoke_sig
        ) THEN
            v_run := gen_random_uuid();
            INSERT INTO monitoring_runs (id, workspace_id, status, trigger_type, notes)
            VALUES (v_run, v_wid, 'completed', 'smoke_rule', '0114 backfill smoke tx=' || left(v_tx, 20));

            -- Insert order mirrors the app (detection → alert → link) to satisfy the
            -- circular FK (alerts.detection_id ⇄ detections.linked_alert_id).
            INSERT INTO detections (
                id, workspace_id, detection_type, severity, confidence, title, evidence_summary,
                evidence_source, source_rule, status, detected_at, raw_evidence_json,
                monitoring_run_id, linked_alert_id
            ) VALUES (
                v_smoke_det, v_wid, 'monitored_wallet_transfer', 'critical', 1.0,
                'Monitored wallet transfer detected: ' || COALESCE(v_name, v_tid::text),
                'Wallet transfer detected on chain ' || v_chain || '.', 'live', 'smoke_wallet_transfer',
                'open', NOW(),
                jsonb_build_object(
                    'event_type', 'wallet_transfer_detected', 'detection_type', 'monitored_wallet_transfer',
                    'tx_hash', v_tx, 'from_address', v_from, 'to_address', v_to, 'amount_wei', v_value,
                    'chain_id', (v_chain)::int, 'block_number', v_block, 'evidence_source', 'live',
                    'telemetry_id', rec.telemetry_id::text, 'target_id', v_tid::text
                ),
                v_run, NULL
            )
            ON CONFLICT (id) DO NOTHING;

            v_smoke_alert := gen_random_uuid();
            INSERT INTO alerts (
                id, workspace_id, user_id, target_id, alert_type, title, severity, status,
                source_service, source, summary, payload, dedupe_signature, detection_id
            ) VALUES (
                v_smoke_alert, v_wid, v_user_id, v_tid, 'threat_monitoring',
                'Monitored wallet transfer detected: ' || COALESCE(v_name, v_tid::text) || ' (chain ' || v_chain || ')',
                'critical', 'open', 'threat-engine', 'live',
                'Wallet transfer detected on chain ' || v_chain || '.',
                jsonb_build_object(
                    'severity', 'critical', 'confidence', 'high', 'source', 'live',
                    'evidence_source', 'live', 'detection_type', 'monitored_wallet_transfer',
                    'recommended_action', 'review_wallet_transfer', 'degraded', false,
                    'reasons', jsonb_build_array('wallet_transfer_detected'),
                    'tx_hash', v_tx, 'from_address', v_from, 'to_address', v_to,
                    'amount_wei', v_value, 'chain_id', (v_chain)::int, 'block_number', v_block,
                    'telemetry_id', rec.telemetry_id::text, 'target_id', v_tid::text,
                    'monitoring_run_id', v_run::text, 'backfill_migration', '0114'
                ),
                v_smoke_sig, v_smoke_det
            );

            UPDATE detections
               SET linked_alert_id = v_smoke_alert, status = 'escalated', updated_at = NOW()
             WHERE id = v_smoke_det;

            v_created := v_created + 1;
        END IF;

        -- ===== Strategic Infrastructure Guard: outbound ETH from monitored wallet =====
        -- Fires only for outbound (from == wallet) Base transfers with value > 0,
        -- mirroring _strategic_infrastructure_guard_alert. Detection seed and
        -- signature carry chain_id as the INTEGER 8453.
        IF v_wallet IS NOT NULL
           AND v_from = lower(v_wallet)
           AND NOT (v_value ~ '^[0-9]+$' AND v_value::numeric = 0)
        THEN
            v_sig_det := _decoda_uuid5_dns_0114(
                'detection:{"chain_id": 8453, "rule": "strategic_infrastructure_guard_wallet_outbound_transfer", "target_id": "'
                || v_tid::text || '", "tx_hash": "' || v_tx || '"}');
            v_sig_sig := replace(_decoda_uuid5_dns_0114(
                '{"chain_id": 8453, "rule": "strategic_infrastructure_guard_wallet_outbound_transfer", "target_id": "'
                || v_tid::text || '", "tx_hash": "' || v_tx || '", "workspace_id": "'
                || v_wid::text || '"}')::text, '-', '');

            IF NOT EXISTS (
                SELECT 1 FROM alerts
                 WHERE workspace_id = v_wid AND target_id = v_tid AND dedupe_signature = v_sig_sig
            ) THEN
                v_run := gen_random_uuid();
                INSERT INTO monitoring_runs (id, workspace_id, status, trigger_type, notes)
                VALUES (v_run, v_wid, 'completed', 'sig_rule', '0114 backfill sig tx=' || left(v_tx, 20));

                -- detection → alert → link (same FK ordering as the smoke block).
                INSERT INTO detections (
                    id, workspace_id, detection_type, severity, confidence, title, evidence_summary,
                    evidence_source, source_rule, status, detected_at, raw_evidence_json,
                    monitoring_run_id, linked_alert_id
                ) VALUES (
                    v_sig_det, v_wid, 'strategic_infrastructure_guard_outbound_transfer', 'critical', 1.0,
                    'Strategic Infrastructure Guard: Treasury RWA Control Wallet Movement Detected',
                    'Outbound ETH movement from a wallet classified as Treasury RWA operational infrastructure.',
                    'live', 'strategic_infrastructure_guard_wallet_outbound_transfer', 'open', NOW(),
                    jsonb_build_object(
                        'evidence_type', 'live_onchain_transaction', 'event_type', 'wallet_transfer_detected',
                        'source', 'rpc_polling', 'detection_type', 'strategic_infrastructure_guard_outbound_transfer',
                        'tx_hash', v_tx, 'from_address', v_from, 'to_address', v_to, 'value_wei', v_value,
                        'chain_id', 8453, 'block_number', v_block, 'evidence_source', 'live',
                        'telemetry_id', rec.telemetry_id::text, 'target_id', v_tid::text,
                        'asset_classification', 'rwa_treasury_control_wallet', 'program', 'Strategic Infrastructure Guard'
                    ),
                    v_run, NULL
                )
                ON CONFLICT (id) DO NOTHING;

                v_sig_alert := gen_random_uuid();
                INSERT INTO alerts (
                    id, workspace_id, user_id, target_id, module_key, alert_type, title, severity, status,
                    source_service, source, summary, payload, dedupe_signature, detection_id
                ) VALUES (
                    v_sig_alert, v_wid, v_user_id, v_tid, 'strategic_infrastructure_guard', 'threat_monitoring',
                    'Strategic Infrastructure Guard: Treasury RWA Control Wallet Movement Detected',
                    'critical', 'open', 'threat-engine', 'rpc_polling',
                    'Outbound ETH movement from a wallet classified as Treasury RWA operational infrastructure.',
                    jsonb_build_object(
                        'severity', 'critical', 'confidence', 'high', 'source', 'rpc_polling',
                        'evidence_source', 'live', 'detection_type', 'strategic_infrastructure_guard_outbound_transfer',
                        'recommended_action', 'review_wallet_transfer', 'degraded', false,
                        'rule_key', 'strategic_infrastructure_guard_wallet_outbound_transfer',
                        'reasons', jsonb_build_array('Outbound ETH movement from a wallet classified as Treasury RWA operational infrastructure.'),
                        'tx_hash', v_tx, 'from_address', v_from, 'to_address', v_to, 'value_wei', v_value,
                        'chain_id', 8453, 'block_number', v_block, 'telemetry_id', rec.telemetry_id::text,
                        'target_id', v_tid::text, 'monitoring_run_id', v_run::text,
                        'evidence_type', 'live_onchain_transaction', 'asset_classification', 'rwa_treasury_control_wallet',
                        'program', 'Strategic Infrastructure Guard', 'backfill_migration', '0114'
                    ),
                    v_sig_sig, v_sig_det
                );

                UPDATE detections
                   SET linked_alert_id = v_sig_alert, status = 'escalated', updated_at = NOW()
                 WHERE id = v_sig_det;

                v_created := v_created + 1;
            END IF;
        END IF;
    END LOOP;

    RAISE NOTICE '0114 backfill complete: % alert(s) created for target %', v_created, v_tid;
END;
$mig$;

DROP FUNCTION IF EXISTS _decoda_uuid5_dns_0114(text);
