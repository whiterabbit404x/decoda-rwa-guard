-- 0152: Screen 9 — indexes for the verifiable evidence package (Merkle seal + history).
--
-- Manifest schema 2.0 seals four new canonical facts into every evidence package:
-- the deterministic SHA-256 Merkle root over its artifact set, the Merkle scheme and
-- hash algorithm that produced it, the artifact count it commits to, and the
-- incident-time policy snapshot that was in force.
--
-- NO NEW COLUMNS AND NO NEW TABLE.
-- ---------------------------------
-- Those facts are persisted exactly where every other canonical evidence-package
-- fact already lives: the existing export_jobs.filters JSONB, next to
-- integrity_hash / manifest_sha256 / evidence_source_fingerprint /
-- supersedes_package_id / completeness_score / verification. That is the
-- convention migration 0141 documents ("no column type changes — both live in the
-- existing export_jobs.filters"), and this migration deliberately follows it rather
-- than forking a parallel package-metadata schema. There is exactly ONE evidence
-- package system in this product and this keeps it that way.
--
-- NOTHING IS BACKFILLED.
-- ----------------------
-- A Merkle root cannot be invented for a package that was sealed without one. A
-- package created before schema 2.0 simply has no merkle_root key; the read path
-- reports "not sealed in this package" and verification treats the Merkle check as
-- not_applicable rather than failed. Backfilling a computed root would attach a
-- commitment to bytes that were never signed with it — the exact untruth the
-- Screen 9 rules forbid.
--
-- What this migration DOES add is read support: two idempotent, additive indexes so
-- the new Export History tab and the auditor's "which package committed to this
-- root?" lookup stay index-served as a workspace's export history grows.
--
-- Backward-safe: additive, IF NOT EXISTS throughout, safe to re-apply, and a
-- rollback needs no data migration.

-- Export History (Screen 9 tab) pages EVIDENCE packages newest-first per workspace.
-- The existing idx_export_jobs_workspace_created covers all export types; this
-- partial index keeps the evidence-package-only listing off the workspace's legacy
-- report/alert/findings export rows as those accumulate.
CREATE INDEX IF NOT EXISTS idx_export_jobs_workspace_evidence_history
    ON export_jobs (workspace_id, created_at DESC)
    WHERE export_type IN ('proof_bundle', 'incident_report');

-- Auditor lookup by sealed Merkle root ("which package committed to this root?").
-- Functional index over the filters JSONB, partial so packages sealed before
-- schema 2.0 (no merkle_root key) are not indexed at all.
CREATE INDEX IF NOT EXISTS idx_export_jobs_merkle_root
    ON export_jobs (workspace_id, (filters ->> 'merkle_root'))
    WHERE (filters ->> 'merkle_root') IS NOT NULL;
