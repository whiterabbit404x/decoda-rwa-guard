-- Digital Forensics Investigator (Screen 7) — deterministic forensic layer.
--
-- Screen 7's "Digital Forensics Investigator" is the automated lead analyst during
-- an active incident. The evidence-grounded AI narrative already lives in the AI
-- Triage Agent tables (migration 0123). This migration adds only the DETERMINISTIC
-- forensic layer that the AI must never invent:
--
--   * A persisted, versioned deterministic analysis (findings, rule matches,
--     workflow stages, confidence + evidence-coverage factors) computed from the
--     immutable, hashed evidence snapshot. Keyed by (incident, snapshot_hash,
--     calc_version) so it is stable across page refreshes and re-derives
--     identically for the same snapshot.
--   * A concurrency-safe per-workspace-year incident reference counter so an
--     incident can carry a human INC-YYYY-NNN number without COUNT(*)+1.
--
-- All DDL is idempotent (IF NOT EXISTS / additive ALTERs) so the startup migration
-- runner can re-apply it safely. Nothing here touches the live telemetry ->
-- detection -> alert -> incident path; every table is new and workspace-scoped,
-- and the only change to an existing table is an additive, NULL-tolerant column.

-- ---------------------------------------------------------------------------
-- Persisted deterministic forensic analysis. The row stores the deterministic
-- facts (never AI-invented): findings with verification state, potential rule
-- matches linked to evidence, workflow stage states, and the deterministic
-- confidence / evidence-coverage with their contributing factors. Because it is
-- keyed by the immutable snapshot_hash + calc_version, re-running the deterministic
-- analyzer on the same snapshot upserts the same row instead of drifting.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS incident_forensic_analyses (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    evidence_snapshot_id UUID NULL REFERENCES incident_evidence_snapshots(id) ON DELETE SET NULL,
    snapshot_hash TEXT NOT NULL,
    calc_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed'
        CHECK (status IN ('completed', 'degraded', 'failed')),
    confidence NUMERIC(4, 3) NULL,
    confidence_band TEXT NULL,
    evidence_coverage NUMERIC(5, 4) NULL,
    findings_count INTEGER NOT NULL DEFAULT 0,
    rule_match_count INTEGER NOT NULL DEFAULT 0,
    analysis_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    degraded_reason TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One persisted analysis per (incident, snapshot, calc version). The DB-facing
-- upsert relies on this to be idempotent under concurrent GET/re-run.
CREATE UNIQUE INDEX IF NOT EXISTS uq_incident_forensic_analyses_key
    ON incident_forensic_analyses (incident_id, snapshot_hash, calc_version);
CREATE INDEX IF NOT EXISTS idx_incident_forensic_analyses_incident
    ON incident_forensic_analyses (workspace_id, incident_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Concurrency-safe incident reference numbering (INC-YYYY-NNN).
--
-- A single atomic UPSERT ... RETURNING per (workspace, year) hands out a gap-free
-- monotonic counter under concurrency. This deliberately replaces any COUNT(*)+1
-- style numbering, which races and reuses numbers when two incidents open at once.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS incident_reference_counters (
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    year INTEGER NOT NULL,
    counter INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (workspace_id, year)
);

-- ---------------------------------------------------------------------------
-- Additive, NULL-tolerant human incident reference (e.g. INC-2026-017). Existing
-- rows stay NULL; the forensic investigation layer assigns one lazily/at
-- auto-creation. Uniqueness is per-workspace and only constrains populated rows.
-- ---------------------------------------------------------------------------
ALTER TABLE incidents
    ADD COLUMN IF NOT EXISTS reference TEXT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_incidents_reference
    ON incidents (workspace_id, reference)
    WHERE reference IS NOT NULL;
