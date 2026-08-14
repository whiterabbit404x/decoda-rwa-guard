-- 0141: Evidence package idempotency keyed on the canonical SOURCE SNAPSHOT
--       (Screen 9 — Evidence & Audit).
--
-- Incident-scoped proof-bundle creation (pilot.create_proof_bundle_export) was
-- idempotent per (workspace, incident, incident-scope) alone. After the canonical
-- evidence collector was repaired to resolve an incident's linked alert, telemetry
-- and investigation timeline, a second "Create Evidence Package" click for the same
-- incident still resolved to the historical package built from the OLD, partial
-- snapshot (EV-2026-004) and never created a superseding package.
--
-- Idempotency is now keyed additionally on a deterministic source-snapshot
-- fingerprint (pilot._evidence_source_fingerprint) — a SHA-256 over the identities
-- and version markers of the canonical source records the collector resolves
-- (incident, canonically linked alert(s), telemetry, detections, response actions,
-- investigation timeline). Two NEW display-only filters keys carry the identity and
-- lineage; no column type changes — both live in the existing export_jobs.filters
-- JSONB, alongside evidence_source_type / export_status / completeness_score:
--
--   filters->>'evidence_source_fingerprint'  'sha256:<hex>' — the snapshot identity
--                                            the create/reuse decision keyed on.
--   filters->>'supersedes_package_id'        internal id of the prior package this
--                                            one supersedes (set only when a changed
--                                            snapshot minted a NEW package; the prior
--                                            row is preserved, never rewritten).
--
-- Behavior (unchanged rows are never rewritten — historical evidence is immutable):
--   * same fingerprint, healthy artifact  -> reuse existing package (created=false)
--   * same fingerprint, stale/missing/failed artifact -> regenerate into the SAME id
--   * different (or absent, i.e. legacy) fingerprint -> create a NEW package that
--     supersedes the incident's prior latest package
--
-- Supporting partial index for the fingerprint reuse lookup. As an incident
-- accumulates superseding packages over time, this keeps the create-or-reuse SELECT
-- (workspace + incident + fingerprint, proof_bundle only) index-served rather than
-- scanning the workspace's full export history. Idempotent and additive.
CREATE INDEX IF NOT EXISTS idx_export_jobs_incident_source_fingerprint
    ON export_jobs (
        workspace_id,
        (filters->>'incident_id'),
        (filters->>'evidence_source_fingerprint')
    )
    WHERE export_type = 'proof_bundle';
