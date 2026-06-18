-- Migration 0115: Add opened_at to alerts and auto-promote Strategic Guard wallet-transfer alerts.
--
-- Root cause: Strategic Guard / smoke wallet-transfer alerts may have been created with a
-- non-terminal status (new/active/created/…) that does NOT literally equal 'open', which can
-- cause them to be hidden under the 'Open' quick filter and count cards on the main Alerts page
-- even though the read-path normalisation layer (Python) maps those statuses to 'open'.
--
-- The two known affected alert IDs for workspace 1155f479…/target e7851a52…:
--   e39485a5-2652-4950-9141-2aa6fe79bea1  (SIG rule, status='new', severity='CRITICAL')
--   3fe45390-3723-4b31-bb76-60fc6666e4fd  (smoke rule, status='active')
--
-- Fix strategy:
--   1. Add an `opened_at` column to the alerts table. When set, it is the canonical timestamp
--      at which the alert was opened/promoted. The list_alerts query treats
--      `opened_at IS NOT NULL` as equivalent to `status = 'open'` — this surface is
--      truthful and does not require the user to click "Open Alert".
--
--   2. Promote every live wallet-transfer alert that is in a non-terminal open-equivalent
--      status: set opened_at = created_at (idempotent), status = 'open',
--      severity = 'critical', source = 'live'.
--
-- Truthfulness rules preserved:
--   - Only evidence_source = 'live' (or source = 'live' / 'rpc_polling') alerts are promoted.
--   - Simulator / replay / demo alerts are never touched.
--   - Terminal statuses (resolved / suppressed / false_positive / acknowledged) are preserved.
--   - No alert is labelled live if its payload evidence_source is 'simulator'.
--
-- Idempotent: safe to re-run. opened_at is set to created_at only when currently NULL.
-- Workspace-scoped: touches only wallet-transfer rule alerts with live evidence.

-- ── 1. Schema change ─────────────────────────────────────────────────────────
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS opened_at TIMESTAMPTZ NULL;

-- Fast lookup for the list_alerts 'opened' gate.
CREATE INDEX IF NOT EXISTS idx_alerts_opened_at_workspace
    ON alerts (workspace_id, opened_at)
    WHERE opened_at IS NOT NULL;

-- ── 2. Promote qualifying live wallet-transfer alerts ────────────────────────
-- Conditions:
--   • wallet-transfer rule: payload->>'rule_key' IN (smoke, SIG) OR module_key = SIG
--     OR payload->>'detection_type' IN (monitored_wallet_transfer, SIG outbound)
--     OR payload carries a tx_hash (belt-and-braces for older rows)
--   • evidence_source is live: payload->>'evidence_source' = 'live'
--     OR source IN ('live', 'rpc_polling') (SIG alerts use source='rpc_polling')
--   • tx_hash exists in payload or evidence sub-object
--   • status is a non-terminal open-equivalent (not resolved/suppressed/acknowledged/etc.)
--   • opened_at is not yet set (idempotency)
UPDATE alerts
SET
    opened_at = created_at,
    status    = 'open',
    severity  = 'critical',
    source    = 'live',
    title     = CASE
                    WHEN title IS NULL OR title = ''
                    THEN 'Strategic Infrastructure Guard: Treasury RWA Control Wallet Movement Detected'
                    ELSE title
                END
WHERE
    (
        payload->>'rule_key' IN (
            'strategic_infrastructure_guard_wallet_outbound_transfer',
            'smoke_wallet_transfer'
        )
        OR module_key = 'strategic_infrastructure_guard'
        OR payload->>'detection_type' IN (
            'strategic_infrastructure_guard_outbound_transfer',
            'monitored_wallet_transfer'
        )
        OR COALESCE(
            payload->>'tx_hash',
            payload->'evidence'->>'tx_hash'
        ) IS NOT NULL
    )
    AND (
        COALESCE(payload->>'evidence_source', '') = 'live'
        OR source IN ('live', 'rpc_polling')
        OR source_service = 'threat-engine'
    )
    AND COALESCE(
        payload->>'tx_hash',
        payload->'evidence'->>'tx_hash'
    ) IS NOT NULL
    AND (
        lower(COALESCE(status, '')) IN (
            '', 'open', 'active', 'new', 'created', 'linked',
            'detection', 'pending', 'none', 'null'
        )
    )
    AND opened_at IS NULL;
