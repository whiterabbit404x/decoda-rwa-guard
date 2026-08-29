-- Operational Integrity detections (Screen 5 — Threat Monitoring).
--
-- The architectural principle this migration encodes:
--
--     Cryptographically valid does NOT mean operationally authorized.
--
-- A mint can carry a valid signature and succeed on-chain while no authorized
-- issuance, no cleared settlement, and no transfer-agent record explain it. That
-- is an OPERATIONAL anomaly, not a cryptographic one, and it needs different
-- columns than the behavioral/cyber detections already stored here: the observed
-- vs expected business amounts, the deterministic checks that produced the
-- verdict, the reason code, and the telemetry provenance of the on-chain event.
--
-- Design decisions:
--   * ADDITIVE ONLY. Every statement is IF NOT EXISTS / ADD COLUMN IF NOT EXISTS
--     so the startup migration runner can re-apply it safely, and every existing
--     row keeps working with a truthful default.
--   * NO PARALLEL TABLE. Operational-integrity detections live in the EXISTING
--     threat_detections table so one canonical event flows
--     detection -> alert -> incident -> response -> evidence. The existing
--     UNIQUE (workspace_id, cluster_key) constraint remains the idempotency key,
--     so repeated telemetry for the same transaction updates one row.
--   * Amounts are NUMERIC(78, 0) BASE UNITS (uint256 range) with an explicit
--     decimals/unit, never binary floating point.
--   * telemetry_stage records what the platform ACTUALLY received
--     (FINALIZED / CONFIRMED / PRECONFIRMATION / UNKNOWN). It is written from
--     runtime provenance, so the UI can never claim a preconfirmation the
--     ingestion path did not deliver.

-- ---------------------------------------------------------------------------
-- category — the first-class detection category. 'CYBER_SECURITY' covers the
--   existing behavioral/exploit detectors; 'OPERATIONAL_INTEGRITY' covers
--   business-state reconciliation findings. Kept as a distinct column rather
--   than derived from detection_type, because the same type (e.g. a mint/burn
--   irregularity) can be reached from either lane.
-- ---------------------------------------------------------------------------
ALTER TABLE threat_detections ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'CYBER_SECURITY';

-- Deterministic verdict fields. reason code + checks are engine output; the AI
-- layer writes only ai_summary and never these.
ALTER TABLE threat_detections ADD COLUMN IF NOT EXISTS deterministic_reason_code TEXT NULL;
ALTER TABLE threat_detections ADD COLUMN IF NOT EXISTS operational_checks JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE threat_detections ADD COLUMN IF NOT EXISTS matcher_version TEXT NULL;

-- Observed vs expected business state, in base units (never float).
ALTER TABLE threat_detections ADD COLUMN IF NOT EXISTS observed_amount NUMERIC(78, 0) NULL;
ALTER TABLE threat_detections ADD COLUMN IF NOT EXISTS expected_amount NUMERIC(78, 0) NULL;
ALTER TABLE threat_detections ADD COLUMN IF NOT EXISTS variance_amount NUMERIC(78, 0) NULL;
ALTER TABLE threat_detections ADD COLUMN IF NOT EXISTS amount_decimals INTEGER NULL;
ALTER TABLE threat_detections ADD COLUMN IF NOT EXISTS amount_unit TEXT NULL;
ALTER TABLE threat_detections ADD COLUMN IF NOT EXISTS operation TEXT NULL;

-- Provenance of the on-chain observation behind the detection.
ALTER TABLE threat_detections ADD COLUMN IF NOT EXISTS tx_hash TEXT NULL;
ALTER TABLE threat_detections ADD COLUMN IF NOT EXISTS block_number BIGINT NULL;
ALTER TABLE threat_detections ADD COLUMN IF NOT EXISTS telemetry_source TEXT NULL;
ALTER TABLE threat_detections ADD COLUMN IF NOT EXISTS telemetry_stage TEXT NULL;
ALTER TABLE threat_detections ADD COLUMN IF NOT EXISTS telemetry_observed_at TIMESTAMPTZ NULL;
ALTER TABLE threat_detections ADD COLUMN IF NOT EXISTS preconfirmation_received_at TIMESTAMPTZ NULL;
ALTER TABLE threat_detections ADD COLUMN IF NOT EXISTS provenance JSONB NOT NULL DEFAULT '{}'::jsonb;

-- ---------------------------------------------------------------------------
-- Backfill: rows already emitted by the Screen 3 reconciliation engine ARE
--   operational-integrity events (that engine already stamps
--   category='OPERATIONAL_INTEGRITY' in its canonical payload). Identified by
--   the engine marker it writes into score_inputs, so no behavioral/cyber
--   detection is ever relabelled. Idempotent.
-- ---------------------------------------------------------------------------
UPDATE threat_detections
   SET category = 'OPERATIONAL_INTEGRITY',
       deterministic_reason_code = COALESCE(deterministic_reason_code, NULLIF(score_inputs->>'reason_code', ''))
 WHERE category = 'CYBER_SECURITY'
   AND score_inputs->>'engine' = 'asset_integrity_reconciliation';

-- ---------------------------------------------------------------------------
-- Indexes. The Screen 5 Detections tab filters by (workspace, category) inside
--   the selected window; the tx-hash index supports provenance lookups and the
--   idempotency probe that keeps repeated telemetry for one transaction from
--   creating duplicate detections.
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_threat_detections_workspace_category_detected
    ON threat_detections (workspace_id, category, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_threat_detections_workspace_tx_hash
    ON threat_detections (workspace_id, tx_hash)
    WHERE tx_hash IS NOT NULL;
