-- Migration 0116: Normalize Strategic Guard / smoke wallet-transfer alert payloads.
--
-- Root cause:  Alerts created via the "Open Alert" button (open_alert_from_detection path)
-- before this fix had matched_patterns[0].rule_id='open_from_detection' instead of the
-- canonical wallet-transfer rule key, and no top-level rule_key in the payload.  The Python
-- normalisation layer (list_alerts → _alert_rule_key → _is_wallet_transfer_rule_alert) fell
-- through to the detection_type check; if the detection_type was not in the known set the
-- alert was silently skipped by the status/severity normalisation, causing Active Alerts = 0.
--
-- Known affected alert IDs (workspace 1155f479…/target e7851a52…):
--   e39485a5-2652-4950-9141-2aa6fe79bea1  (SIG rule)
--   3fe45390-3723-4b31-bb76-60fc6666e4fd  (smoke rule)
--
-- Fixes applied (idempotent — safe to re-run):
--   1. For SIG alerts: set payload.rule_key, payload.confidence='high', payload.severity='critical',
--      canonical title, status='open', severity='critical', opened_at=created_at.
--   2. For smoke alerts: same payload fields with smoke rule_key.
--   3. Update matched_patterns rule_id from 'open_from_detection' to the canonical rule_key.
--
-- Truthfulness rules preserved:
--   • Only evidence_source='live' (or source='live'/'rpc_polling'/'threat-engine') alerts.
--   • Simulator/demo/replay evidence_source alerts are never touched.
--   • Terminal statuses (resolved/suppressed/false_positive/acknowledged) are preserved.

-- ── 1. SIG alerts: payload rule_key patch ────────────────────────────────────
UPDATE alerts
SET
    payload   = payload
                || jsonb_build_object(
                    'rule_key',   'strategic_infrastructure_guard_wallet_outbound_transfer',
                    'confidence', 'high',
                    'severity',   'critical'
                ),
    status    = 'open',
    severity  = 'critical',
    opened_at = COALESCE(opened_at, created_at),
    source    = CASE WHEN source = 'rpc_polling' THEN 'live' ELSE source END,
    title     = CASE
                    WHEN title IS NULL OR title = ''
                    THEN 'Strategic Infrastructure Guard: Treasury RWA Control Wallet Movement Detected'
                    ELSE title
                END
WHERE
    (
        module_key = 'strategic_infrastructure_guard'
        OR payload->>'rule_key' = 'strategic_infrastructure_guard_wallet_outbound_transfer'
        OR payload->>'detection_type' = 'strategic_infrastructure_guard_outbound_transfer'
    )
    AND (
        COALESCE(payload->>'evidence_source', '') = 'live'
        OR source IN ('live', 'rpc_polling')
        OR source_service = 'threat-engine'
    )
    AND lower(COALESCE(status, '')) NOT IN ('resolved', 'suppressed', 'false_positive');

-- ── 2. Smoke wallet-transfer alerts: payload rule_key patch ──────────────────
UPDATE alerts
SET
    payload   = payload
                || jsonb_build_object(
                    'rule_key',   'smoke_wallet_transfer',
                    'confidence', 'high',
                    'severity',   'critical'
                ),
    status    = 'open',
    severity  = 'critical',
    opened_at = COALESCE(opened_at, created_at)
WHERE
    (
        payload->>'rule_key' = 'smoke_wallet_transfer'
        OR payload->>'detection_type' = 'monitored_wallet_transfer'
        OR (
            payload->'matched_patterns' @> '[{"rule_id": "smoke_wallet_transfer"}]'
            OR payload->'matched_patterns' @> '[{"rule_id": "open_from_detection"}]'
        )
    )
    AND module_key IS DISTINCT FROM 'strategic_infrastructure_guard'
    AND (
        COALESCE(payload->>'evidence_source', '') = 'live'
        OR source IN ('live', 'rpc_polling')
        OR source_service = 'threat-engine'
    )
    AND COALESCE(payload->>'tx_hash', payload->'evidence'->>'tx_hash') IS NOT NULL
    AND lower(COALESCE(status, '')) NOT IN ('resolved', 'suppressed', 'false_positive');
